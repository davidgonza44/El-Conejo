"""Read-only localhost MySQL acceptance: real HTTP, real models, real process restart.

Uses a new disposable cache, synthetic authentication and GET requests only.
Never seeds MySQL or opens existing instance/cache files. Prints aggregate evidence only.
Run with the project Python; normal application configuration is validated before connect.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PAGES = ("demand", "replenishment", "critical", "trends", "accuracy")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def serve(cache_root):
    from sqlalchemy import event
    from sqlalchemy.engine import make_url
    from flask import jsonify
    from werkzeug.serving import make_server, WSGIRequestHandler
    from app import create_app
    from app.config import Config
    from app.extensions import db, login_manager
    from app.services import predictive_cache_service as cache
    from app.services import predictive_job_service as jobs
    from app.services import predictive_analysis_service as analysis
    from app.services import predictive_data_service as data
    from app.services import session_service

    url = make_url(Config.SQLALCHEMY_DATABASE_URI)
    if (Config.DB_HOST, Config.DB_NAME, url.host, url.database) != (
            "localhost", "ferreteria_conejo", "localhost", "ferreteria_conejo"):
        raise RuntimeError("ABORT: database target is not the authorized localhost database")
    app = create_app()
    app.instance_path = str(Path(cache_root) / "isolated-instance")
    app.config.update(PREDICTIVE_CACHE_DIR=cache_root, SECRET_KEY=os.urandom(32),
                      MAIL_ENABLED=False, NOTIFICATIONS_DRY_RUN=True, TESTING=True)
    # In this dedicated test process only: no login writes or session activity touches.
    app.before_request_funcs[None].remove(session_service._validate_request)
    actor = SimpleNamespace(id=0, name="Verificacion local", username="verification",
                            role="admin", profile_photo_url=None, is_authenticated=True,
                            is_active=True, is_anonymous=False)
    login_manager.request_loader(lambda request: actor)
    with app.app_context():
        engine = db.engine
    stats = dict(worker_checkouts=0, model_pool_min=None, model_pool_max=0,
                 model_calls=0, model_seconds=0.0, worker_context=False,
                 worker_sql=0, blocked_writes=0, timeout_errors=0, keys=[],
                 background_start=None, background_end=None)

    @event.listens_for(engine, "connect")
    def readonly(connection, record):
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")

    @event.listens_for(engine, "before_cursor_execute")
    def check_sql(connection, cursor, statement, parameters, context, many):
        if threading.current_thread().name == "predictive-live":
            stats["worker_sql"] += 1
        if statement.lstrip().split(None, 1)[0].upper() not in ("SELECT", "SHOW", "DESCRIBE"):
            stats["blocked_writes"] += 1
            raise RuntimeError("Read-only verification rejected SQL")

    @event.listens_for(engine, "checkout")
    def checkout(*args):
        if threading.current_thread().name == "predictive-live":
            stats["worker_checkouts"] += 1

    @event.listens_for(engine, "handle_error")
    def sql_error(context):
        if "QueuePool" in type(context.original_exception).__name__ + str(context.original_exception):
            stats["timeout_errors"] += 1

    original_get = cache._Predictions.get

    def measured_get(self, key, compute, periods, **kwargs):
        def measured_compute():
            from flask import has_app_context, has_request_context
            start = time.perf_counter()
            count = engine.pool.checkedout()
            stats["model_pool_min"] = count if stats["model_pool_min"] is None else min(count, stats["model_pool_min"])
            stats["model_pool_max"] = max(count, stats["model_pool_max"])
            stats["worker_context"] |= has_app_context() or has_request_context()
            stats["model_calls"] += 1
            stats["keys"].append(key)
            if stats["background_start"] is None:
                stats["background_start"] = start
            try:
                return compute()
            finally:
                stats["model_seconds"] += time.perf_counter() - start
                stats["background_end"] = time.perf_counter()
        return original_get(self, key, measured_compute, periods, **kwargs)

    cache._Predictions.get = measured_get

    @app.get("/__verification/status")
    def status():
        manager = app.extensions.get(jobs._EXTENSION)
        snapshot = dict(stats, pid=os.getpid(), checkedout=engine.pool.checkedout(),
                        pool=engine.pool.status(), pending=len(manager.pending) if manager else 0,
                        started=manager.started if manager else 0,
                        finished=manager.finished if manager else 0,
                        failures=len(manager.failures) if manager else 0,
                        computed=manager.cache.computed if manager else 0,
                        worker=bool(manager and manager.thread))
        return jsonify(snapshot)

    @app.get("/__verification/domain")
    def domain():
        result = analysis.analyze(release_session=True, defer_predictions=True)
        items = [asdict(item) for item in result.items]
        eligible = [item.product.id for item in result.items if item.eligibility.status == data.ELIGIBLE]
        return jsonify(status=result.computation_status, count=len(items), digest=digest(items),
                       eligible=len(eligible), product=result.items[0].product.id)

    class QuietHandler(WSGIRequestHandler):
        def log_request(self, *args, **kwargs):
            pass

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietHandler)
    print(json.dumps({"port": server.server_port, "pid": os.getpid(), "target_verified": True}), flush=True)
    server.serve_forever()


def get(base, path):
    start = time.perf_counter()
    try:
        response = urlopen(base + path, timeout=30)
    except HTTPError as error:
        response = error
    with response:
        body = response.read()
        return response.status, body, round(time.perf_counter() - start, 4)


def state(base):
    code, body, _ = get(base, "/__verification/status")
    assert code == 200
    return json.loads(body)


def launch(root):
    process = subprocess.Popen([sys.executable, "-u", __file__, "--serve", root],
                               stdout=subprocess.PIPE, text=True, cwd=ROOT)
    line = process.stdout.readline()
    if not line:
        process.wait(10)
        raise RuntimeError("Verification server failed before startup")
    info = json.loads(line)
    assert info["target_verified"]
    return process, f'http://127.0.0.1:{info["port"]}'


def page(base, name):
    code, body, elapsed = get(base, "/predictive/" + name)
    assert code == 200, (name, code)
    return dict(seconds=elapsed, pending=b'data-predictive-status="pending"' in body,
                honest="Calculando pronóstico".encode() in body)


def wait_ready(base, timeout=360):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        snapshot = state(base)
        assert not snapshot["failures"], "Background job failed"
        if not snapshot["pending"]:
            return snapshot
        time.sleep(0.5)
    raise AssertionError("Background calculation timed out")


def domain(base):
    code, body, _ = get(base, "/__verification/domain")
    assert code == 200
    return json.loads(body)


def evidence(label, value):
    print(label + " " + json.dumps(value, sort_keys=True), flush=True)


def acceptance(root):
    processes = []
    try:
        first, base = launch(root)
        processes.append(first)
        cold = page(base, "demand")
        assert cold["pending"] and cold["honest"] and cold["seconds"] < 15
        evidence("A_COLD", cold)
        initial = state(base)
        refreshes = [page(base, "demand") for _ in range(3)]
        assert state(base)["started"] == initial["started"] == 1
        pending_routes = {name: page(base, name) for name in PAGES[1:]}
        export = get(base, "/api/predictions/export/demand_forecast")
        assert export[0] == 202 and "pron" in export[1].decode().lower()
        evidence("B_PENDING", dict(refreshes=refreshes, routes=pending_routes, export_status=export[0]))
        second, other = launch(root)
        processes.append(second)
        other_page = page(other, "demand")
        evidence("B_SECOND_PROCESS", other_page)
        done = wait_ready(base)
        other_done = wait_ready(other)
        assert not set(done["keys"]) & set(other_done["keys"]), "Duplicate model computations across processes"
        assert other_done["computed"] == 0, "Second process repeated valid fits"
        evidence("BACKGROUND", dict(seconds=round(done["background_end"] - done["background_start"], 4),
                                    model_seconds=round(done["model_seconds"], 4), calls=done["model_calls"],
                                    computed=done["computed"], other_computed=other_done["computed"]))
        warm = {name: page(base, name) for name in PAGES}
        before = domain(base)
        assert before["status"] == "ready" and state(base)["model_calls"] == done["model_calls"]
        assert before == domain(other)
        evidence("C_HOT", dict(routes=warm, products=before["count"], eligible=before["eligible"], equality=True))
        product = before["product"]
        detail = get(base, f"/api/predictions/products/{product}/detail")
        assert detail[0] == 200
        detail_body = json.loads(detail[1])
        evidence("DETAIL", dict(seconds=detail[2], status=detail_body["computation_status"]))
        wait_ready(base)
        detail = get(base, f"/api/predictions/products/{product}/detail")
        assert detail[0] == 200 and json.loads(detail[1])["computation_status"] == "ready"
        assert get(base, "/api/predictions/export/demand_forecast")[0] == 200
        final = state(base)
        for snapshot in (final, other_done):
            assert snapshot["checkedout"] == snapshot["worker_checkouts"] == snapshot["worker_sql"] == 0
            assert snapshot["blocked_writes"] == snapshot["timeout_errors"] == 0
            assert not snapshot["worker_context"]
        evidence("POOL", {key: final[key] for key in ("checkedout", "worker_checkouts", "worker_sql",
                 "model_pool_min", "model_pool_max", "pool", "blocked_writes", "timeout_errors", "worker_context")})
        old_pids = [p.pid for p in processes]
        for process in processes:
            process.terminate()
            process.wait(10)
        restarted, again = launch(root)
        processes.append(restarted)
        assert restarted.pid not in old_pids
        restart_page = page(again, "demand")
        after = domain(again)
        restart = state(again)
        assert not restart_page["pending"] and restart_page["seconds"] < 15
        assert after == before and restart["computed"] == restart["started"] == restart["checkedout"] == 0
        assert not restart["worker"]
        evidence("D_REAL_RESTART", dict(page=restart_page, different_pid=True, old_processes_exited=True,
                                       numerical_equality=True, computed=restart["computed"], worker=restart["worker"],
                                       pool=restart["pool"]))
        evidence("RESULT", "PASS")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        serve(args.serve)
    else:
        with tempfile.TemporaryDirectory(prefix="predictive-final-") as directory:
            acceptance(directory)
