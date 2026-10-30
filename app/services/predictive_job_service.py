"""Local LIVE background jobs; materialized series in, predictive blocks out.

One lazy daemon thread per application and one OS lock per cache namespace. Neither
Flask contexts nor business connections enter the worker. Restart cancels pending
work with the process; successfully persisted leaf predictions remain reusable.
"""
from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import errno
import logging
import os
from queue import Queue
from threading import Event, Lock, Thread
import time
import sqlite3

from flask import current_app, has_request_context, request

from app.services import predictive_cache_service as cache_service

if os.name == "nt":
    import msvcrt
else:
    import fcntl

_EXTENSION = "predictive_jobs"
_INIT_LOCK = Lock()
_MAX_JOBS = 16
_MAX_COMPLETED = 16
_RETRY_SECONDS = 30
_TIMEOUT_SECONDS = 1800
_LOG = logging.getLogger(__name__)


def _try_job_lease(directory, key):
    """Nonblocking OS ownership, released even when a worker process crashes.

    Lock files are retained: unlinking a released lock can create two owners on
    different inodes. Their names contain only the opaque input digest.
    """
    leases = directory / "jobs"
    leases.mkdir(exist_ok=True)
    handle = open(leases / (key + ".lock"), "a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        if error.errno not in (errno.EACCES, errno.EAGAIN):
            raise
        return None
    return handle


@contextmanager
def process_lock(directory, stopped):
    """OS-owned lock, not a lease or a SQL transaction; crashes release ownership."""
    with open(directory / "worker.lock", "a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        acquired = False
        while not stopped.is_set() and not acquired:
            handle.seek(0)
            try:
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                stopped.wait(0.1)
        if not acquired:
            raise InterruptedError("Predictive worker stopped")
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _compute(specs, cache, *, read_only=False, stopped=None):
    from app.services.predictive_analysis_service import prediction_block

    blocks = {}
    with cache.batch():
        for series, horizon, model in specs:
            if stopped is not None and stopped.is_set():
                raise InterruptedError("Predictive worker stopped")
            blocks[series.product_id, horizon] = prediction_block(
                series, horizon, model,
                predictor=cache_service.cached_predictor(cache, series.product_id, read_only=read_only))
    return blocks


def _key(specs):
    from app.services import backtest_service, predictive_data_service, forecast_service

    # Source files are hashed once; mutable policy/configuration is also part of the key.
    rows = [(series.product_id, series.granularity, series.values,
             [day.isoformat() for day in series.starts], series.time_unit, horizon, model)
            for series, horizon, model in specs]
    parameters = [(model, cache_service._parameters(model)) for model in forecast_service.MODELS]
    policy = (backtest_service.MAX_FOLDS, backtest_service.MIN_TRAIN_RATIO,
              {name: asdict(value) for name, value in predictive_data_service.ELIGIBILITY_CONFIG.items()},
              cache_service._callable_token(backtest_service.evaluate_models))
    return cache_service.digest((rows, parameters, policy))


class Jobs:
    def __init__(self, cache, timeout=_TIMEOUT_SECONDS):
        self.cache = cache
        self.timeout = max(1, float(timeout))
        self.completed = OrderedDict()
        self.failures = OrderedDict()
        self.pending = set()
        self.lock = Lock()
        self.queue = Queue(maxsize=_MAX_JOBS)
        self.stopped = Event()
        self.idle = Event()
        self.idle.set()
        self.thread = None
        self.started = 0
        self.finished = 0

    @staticmethod
    def _result_status(blocks):
        return "unavailable" if blocks and all(block[0] is None for block in blocks.values()) else "ready"

    def status(self, key):
        """Observe one opaque job across workers; never load demand or enqueue work."""
        row = self.cache.disk.read_job(key)
        if row is None:
            return {"status": "expired", "terminal": True, "retry_after": 2}
        status, updated = row
        timed_out = status in ("pending", "running") and time.time() - updated >= self.timeout
        return {"status": "error" if timed_out else status,
                "terminal": timed_out or status in ("ready", "error", "unavailable"),
                "timed_out": timed_out, "retry_after": 2}

    def _pending_status(self, key):
        status = self.status(key)["status"]
        # Disk publication can precede the in-memory handoff by a few microseconds.
        # A ready response must always carry its blocks, never trigger a sync fit.
        return "running" if status in ("ready", "unavailable") else status

    def _local_result(self, key):
        if key in self.completed:
            self.completed.move_to_end(key)
            blocks = deepcopy(self.completed[key])
            return self._result_status(blocks), blocks
        if key in self.pending:
            return self._pending_status(key), {}
        if key in self.failures and time.monotonic() - self.failures[key] < _RETRY_SECONDS:
            return "error", {}
        return None

    def _probe(self, specs):
        # Cache-only probes never wait for a Future or call a model on a miss.
        try:
            return _compute(specs, self.cache, read_only=True)
        except cache_service.PendingPrediction:
            return None

    def request(self, specs, *, token=None):
        if not specs:
            return "ready", {}
        key = token or _key(specs)
        with self.lock:
            local = self._local_result(key)
        if local is not None:
            return local
        result = self._probe(specs)
        if result is not None:
            return self._result_status(result), result
        with self.lock:
            local = self._local_result(key)
            if local is not None:
                return local
            if self.stopped.is_set() or self.queue.full():
                return "busy", {}
            return self._enqueue(key, specs)

    def _enqueue(self, key, specs):
        """Called under self.lock; OS ownership also excludes other processes."""
        lease = _try_job_lease(self.cache.disk.directory, key)
        if lease is None:
            # Atomic status publication can lag ownership by a few microseconds.
            state = self.status(key)["status"]
            return (state if state in ("pending", "running", "error") else "running"), {}
        try:
            # The previous owner may have finished after our first probe.
            result = self._probe(specs)
            if result is not None:
                lease.close()
                return self._result_status(result), result
            self.cache.disk.write_job(key, "pending")
            self.queue.put_nowait((key, deepcopy(specs), lease))
        except BaseException:
            lease.close()
            raise
        self.pending.add(key)
        self.idle.clear()
        if self.thread is None:
            self.thread = Thread(target=self._run, name="predictive-live", daemon=True)
            self.thread.start()
        return "pending", {}

    def _run(self):
        while not self.stopped.is_set():
            job = self.queue.get()
            if job is None:
                self.queue.task_done()
                break
            key, specs, lease = job
            try:
                with process_lock(self.cache.disk.directory, self.stopped):
                    self.cache.disk.write_job(key, "running")
                    self.started += 1
                    # The other process may already have populated every successful leaf.
                    result = _compute(specs, self.cache, stopped=self.stopped)
                    self.cache.disk.write_job(key, self._result_status(result))
                with self.lock:
                    self.completed[key] = result
                    self.completed.move_to_end(key)
                    while len(self.completed) > _MAX_COMPLETED:
                        self.completed.popitem(last=False)
                    self.failures.pop(key, None)
                    self.finished += 1
            except Exception as error:
                # No exception details, user input, or errors enter disk/result caches.
                _LOG.warning("Operational predictive calculation did not complete (%s); a later refresh can retry.",
                             type(error).__name__)
                self._record_failure(key)
                with self.lock:
                    self.failures[key] = time.monotonic()
                    while len(self.failures) > _MAX_COMPLETED:
                        self.failures.popitem(last=False)
            finally:
                with self.lock:
                    self.pending.discard(key)
                    if not self.pending:
                        self.idle.set()
                lease.close()
                self.queue.task_done()

    def _record_failure(self, key):
        try:
            self.cache.disk.write_job(key, "error")
        except (OSError, sqlite3.Error) as error:
            # If the scratch store failed too, polling reaches its bounded deadline.
            _LOG.warning("Operational predictive status could not be persisted (%s).", type(error).__name__)

    def wait(self, timeout=300):
        """Explicit prewarming/tests only. HTTP callers must never call this method."""
        return self.idle.wait(timeout)

    def shutdown(self, timeout=0):
        """Stop between products; process exit cannot leave this daemon orphaned."""
        self.stopped.set()
        with self.lock:
            while not self.queue.empty():
                job = self.queue.get_nowait()
                if job is not None:
                    self.pending.discard(job[0])
                    self._record_failure(job[0])
                    job[2].close()
                self.queue.task_done()
            if self.thread is not None:
                self.queue.put_nowait(None)
            if not self.pending:
                self.idle.set()
        if self.thread is not None and timeout:
            self.thread.join(timeout)


def get_jobs(app=None):
    app = app or current_app._get_current_object()
    with _INIT_LOCK:
        if _EXTENSION not in app.extensions:
            app.extensions[_EXTENSION] = Jobs(cache_service.get_cache(app, persistent=True),
                timeout=app.config.get("PREDICTIVE_JOB_TIMEOUT_SECONDS", _TIMEOUT_SECONDS))
        return app.extensions[_EXTENSION]


def prediction_token(specs):
    return _key(specs) if specs else None


def prediction_status(token):
    try:
        return get_jobs().status(token)
    except (OSError, ValueError, sqlite3.Error) as error:
        _LOG.warning("Operational predictive status could not be read (%s).", type(error).__name__)
        return {"status": "error", "terminal": True, "retry_after": 2}


def request_predictions(specs, *, token=None):
    if has_request_context() and request.args.get("ref") == "1":
        raise ValueError("Reference mode cannot schedule LIVE predictions")
    try:
        return get_jobs().request(specs, token=token)
    except (OSError, ValueError, sqlite3.Error) as error:
        _LOG.warning("Operational predictive job could not be requested (%s).", type(error).__name__)
        return "error", {}
