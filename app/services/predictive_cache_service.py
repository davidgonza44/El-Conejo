"""Bounded LIVE prediction cache: numeric successes only, never decisions or ORM state.

Persistent keys bind the complete training input and the loaded implementation. The
optional disk store is private operational scratch, not an empirical-results store.
"""
from collections import OrderedDict
from concurrent.futures import Future
from contextlib import closing, contextmanager
from functools import lru_cache, partial
import hashlib
from importlib.metadata import version
import json
import marshal
import math
from pathlib import Path
import sqlite3
from threading import Lock, local
import time

from flask import current_app, has_request_context, request
from sqlalchemy.engine import make_url
from threadpoolctl import threadpool_limits

from app.services import forecast_service as forecast
from app.services import forecast_advanced_service as advanced
from app.services import experimental_demand_service as experimental

_MAX_ENTRIES = 2048
_MAX_VALUE_BYTES = 65536
_MAX_FITS = 64
_EXTENSION = "predictive_prediction_cache"
_INIT_LOCK = Lock()
_NATIVE_THREADS_LOCK = Lock()
_LIVE_BLAS_THREADS = 1  # DEMO 1/2/4/8 probe: lowest aggregate wall time at 1.


class PendingPrediction(Exception):
    """A cache miss, NOT an unavailable model or a forecast of zero."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def _valid(values, periods):
    return (isinstance(values, (list, tuple)) and len(values) == periods
            and all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values))


class _Disk:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "predictions.sqlite3"
        with closing(self.connect()) as connection, connection:
            # Regenerable scratch: WAL avoids a durable fsync per small prediction.
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS predictions "
                               "(key TEXT PRIMARY KEY, value TEXT NOT NULL, used REAL NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS job_states "
                               "(key TEXT PRIMARY KEY, status TEXT NOT NULL, updated REAL NOT NULL)")

    def connect(self):
        # Connections exist only for short reads/writes, never for fitting models.
        connection = sqlite3.connect(self.path, timeout=0.25)
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def read_job(self, key):
        with closing(self.connect()) as connection:
            return connection.execute("SELECT status, updated FROM job_states WHERE key = ?", (key,)).fetchone()

    def write_job(self, key, status, *, preserve_active=False):
        # Only opaque input digests and lifecycle metadata cross process boundaries.
        statement = "INSERT OR REPLACE INTO job_states VALUES (?, ?, ?)"
        if preserve_active:
            statement = ("INSERT INTO job_states VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                         "status=excluded.status, updated=excluded.updated "
                         "WHERE job_states.status NOT IN ('pending', 'running')")
        with closing(self.connect()) as connection, connection:
            connection.execute(statement, (key, status, time.time()))
            connection.execute("DELETE FROM job_states WHERE key IN "
                               "(SELECT key FROM job_states ORDER BY updated DESC LIMIT -1 OFFSET 128)")

    def read(self, key, periods):
        with closing(self.connect()) as connection, connection:
            row = connection.execute("SELECT value FROM predictions WHERE key = ?", (key,)).fetchone()
            if row is None:
                return None
            try:
                values = json.loads(row[0]) if len(row[0]) <= _MAX_VALUE_BYTES else None
            except (TypeError, ValueError):
                values = None
            if not _valid(values, periods):
                connection.execute("DELETE FROM predictions WHERE key = ?", (key,))
                return None
            # Disk eviction uses the last computation time. A hit is a read, not
            # another durable transaction; the in-process cache keeps exact LRU.
            return tuple(values)

    def write(self, key, values):
        self.write_many({key: values})

    def write_many(self, values):
        rows = []
        for key, value in values.items():
            payload = json.dumps(value, allow_nan=False, separators=(",", ":"))
            if len(payload) <= _MAX_VALUE_BYTES:
                rows.append((key, payload, time.time()))
        if not rows:
            return
        with closing(self.connect()) as connection, connection:
            connection.executemany("INSERT OR REPLACE INTO predictions VALUES (?, ?, ?)", rows)
            connection.execute("DELETE FROM predictions WHERE key IN "
                               "(SELECT key FROM predictions ORDER BY used DESC LIMIT -1 OFFSET ?)",
                               (_MAX_ENTRIES,))


class _Predictions:
    def __init__(self, directory=None):
        self.values = OrderedDict()
        self.pending = {}
        self.lock = Lock()
        self.disk = _Disk(directory) if directory is not None else None
        self.computed = 0
        self.hits = 0
        self.fits = OrderedDict()
        self.fit_lock = Lock()
        self._batch = local()

    @contextmanager
    def batch(self):
        """Buffer only successful numeric paths; no SQL transaction spans a fit.

        Nested callers share the thread's buffer. Flush even on a later model
        failure so completed work survives restart; disk errors remain visible.
        """
        if getattr(self._batch, "values", None) is not None:
            yield
            return
        self._batch.values = {}
        try:
            yield
        finally:
            values = self._batch.values
            self._batch.values = None
            if self.disk and values:
                self.disk.write_many(values)

    def _persist(self, key, result):
        values = getattr(self._batch, "values", None)
        if values is None:
            self.disk.write(key, result)
        else:
            values[key] = result
            if len(values) >= _MAX_ENTRIES:
                self.disk.write_many(values)
                values.clear()

    def fitted(self, key, compute):
        """Bounded process-only TRAIN fits; never pickle estimators to disk.

        A separate lock serializes cold fits without blocking numeric cache reads.
        Exceptions are not retained, so unavailable/transient fits remain retryable.
        """
        with self.fit_lock:
            if key not in self.fits:
                # Native limits are process-wide. Serialize scopes across app
                # cache instances and restore the prior limit after the LIVE fit.
                with _NATIVE_THREADS_LOCK, threadpool_limits(limits=_LIVE_BLAS_THREADS, user_api="blas"):
                    self.fits[key] = compute()
            self.fits.move_to_end(key)
            while len(self.fits) > _MAX_FITS:
                self.fits.popitem(last=False)
            return self.fits[key]

    def _remember(self, key, result):
        self.values[key] = result
        self.values.move_to_end(key)
        while len(self.values) > _MAX_ENTRIES:
            self.values.popitem(last=False)

    def get(self, key, compute, periods, *, read_only=False, persistent=True):
        with self.lock:
            result = self.values.get(key)
            if result is None and self.disk and persistent:
                result = self.disk.read(key, periods)
            if result is not None:
                self._remember(key, result)
                self.hits += 1
                return list(result)
            if read_only:
                raise PendingPrediction()
            future = self.pending.get(key)
            owner = future is None
            if owner:
                future = self.pending[key] = Future()
        if not owner:
            return list(future.result())
        try:
            result = tuple(compute())
            with self.lock:
                self.computed += 1
                if _valid(result, periods):
                    if self.disk and persistent:
                        self._persist(key, result)
                    self._remember(key, result)
                future.set_result(result)
                self.pending.pop(key)
        except BaseException as error:
            with self.lock:
                future.set_exception(error)
                self.pending.pop(key)
            raise
        return list(result)


@lru_cache(maxsize=1)
def _implementation():
    sources = [hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
               for module in (forecast, advanced, experimental)]
    packages = [(name, version(name)) for name in ("numpy", "scipy", "statsmodels", "scikit-learn")]
    return sources, packages


@lru_cache(maxsize=128)
def _callable_token(value):
    code = getattr(value, "__code__", None)
    if code is not None and type(code).__name__ == "code":
        return (value.__module__, value.__qualname__, hashlib.sha256(marshal.dumps(code)).hexdigest())
    if isinstance(value, type):
        return value.__module__, value.__qualname__
    # Test doubles / runtime replacements must never reuse or pollute persisted output.
    return "runtime", id(value)


def _parameters(model):
    return (_implementation(), tuple(_callable_token(value) for value in
            (forecast.predict, forecast.MODELS[model], advanced.ARIMA, advanced.RandomForestRegressor)),
            tuple(sorted(forecast.WINDOW.items())), tuple(advanced.ARIMA_ORDERS),
            tuple((name, tuple(sorted(values.items()))) for name, values in advanced.MODEL_REQUIREMENTS.items()),
            tuple(sorted(advanced.RF_LAGS.items())), tuple(sorted(advanced.RF_WINDOW.items())),
            _LIVE_BLAS_THREADS)


def _predict(cache, product_id, history, periods, model, granularity="daily", starts=None,
             *, operational=False, future_starts=None, read_only=False):
    if operational:
        return forecast.predict(history, periods, model, granularity, starts=starts,
                                operational=True, future_starts=future_starts)
    parameters = _parameters(model)
    training = (product_id, tuple(history), model, granularity, "calendar_day",
                None if starts is None else tuple(d.isoformat() for d in starts), parameters)
    training_key = digest(training)
    key = digest((training_key, periods))
    persistent = all(token[0] != "runtime" for token in parameters[1])
    options = ({"fit_cache": partial(cache.fitted, training_key)}
               if model in advanced.MODEL_REQUIREMENTS and persistent else {})
    return cache.get(key, lambda: forecast.predict(history, periods, model, granularity, starts=starts, **options),
                     periods, read_only=read_only, persistent=persistent)


def namespace(app):
    uri = app.config.get("SQLALCHEMY_DATABASE_URI")
    if not uri:
        return digest((app.name, app.root_path))
    url = make_url(uri)
    # No credentials or raw connection string are stored in keys or filenames.
    return digest((url.drivername, url.host, url.port, url.database))


def get_cache(app=None, *, persistent=False):
    app = app or current_app._get_current_object()
    with _INIT_LOCK:
        cache = app.extensions.get(_EXTENSION)
        if cache is None:
            cache = app.extensions[_EXTENSION] = _Predictions()
        if persistent and cache.disk is None:
            directory = Path(app.config.get("PREDICTIVE_CACHE_DIR") or
                             Path(app.instance_path) / "predictive_cache") / namespace(app)
            cache.disk = _Disk(directory)
        return cache


def cached_predictor(cache, product_id, *, read_only=False):
    """Context-free predictor for materialized LIVE jobs and read-only HTTP probes."""
    return partial(_predict, cache, product_id, read_only=read_only)


def predictor(product_id):
    """Preserve synchronous opt-in for existing direct callers; science bypasses it."""
    if not has_request_context() or request.args.get("ref") == "1":
        return None
    return cached_predictor(get_cache(), product_id)
