"""HTTP prediction verification restricted to the existing DEMO 5001 environment.

Cold/hot/restart describe externally prepared phases; this harness never clears a
cache, restarts services or modifies demand. Only normal authentication sessions
are written. Reports contain timings, counts, statuses and synthetic product IDs;
cookies, opaque job tokens, HTML and business response bodies remain in memory.
"""
import argparse
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
from threading import Event, Thread
import time
from urllib.parse import urlencode

import requests
from sqlalchemy import text

from docker_support.guard import validate_demo, validate_demo_url

BASE = "http://host.docker.internal:5001"
METHODS = ("naive", "mean", "moving_average", "linear_trend", "arima", "random_forest", "auto")
HORIZONS = (7, 30, 90)
ACTIVE = {"pending", "running"}
TERMINAL = {"ready", "unavailable", "error", "expired", "busy"}
STATUS_PATH = re.compile(r"/api/predictions/status/[a-f0-9]{64}\Z")


class PredictionMarkup(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.status, self.status_url = "ready", None
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "data-predictive-status" in values:
            self.status = values["data-predictive-status"]
            self.status_url = values.get("data-predictive-status-url")


def request(client, path):
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        raise ValueError("Only a relative DEMO endpoint is allowed")
    began = time.perf_counter()
    response = client.get(BASE + path, timeout=(5, 45), allow_redirects=False)
    elapsed = time.perf_counter() - began
    if response.status_code != 200:
        raise RuntimeError("Unexpected HTTP response")
    return response, elapsed


def summary(samples):
    if not samples:
        return {"count": 0}
    ordered = sorted(samples)
    return {"count": len(samples), "mean_seconds": sum(samples) / len(samples),
            "median_seconds": ordered[len(ordered) // 2],
            "p95_seconds": ordered[math.ceil(.95 * len(ordered)) - 1],
            "max_seconds": max(samples)}


class Responsiveness:
    def __init__(self, source):
        self.client = requests.Session()
        self.client.trust_env = False
        self.client.cookies.update(source.cookies)
        self.stop = Event()
        self.active = Event()
        self.samples, self.active_samples, self.errors = [], [], []
        self.thread = Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop.is_set():
            try:
                active_before = self.active.is_set()
                _, elapsed = request(self.client, "/api/auth/me")
                self.samples.append(elapsed)
                if active_before and self.active.is_set():
                    self.active_samples.append(elapsed)
            except requests.RequestException as error:
                self.errors.append(type(error).__name__)
            except RuntimeError:
                self.errors.append("HTTPStatusError")
            self.stop.wait(1)

    def finish(self):
        self.stop.set()
        self.thread.join(50)
        self.client.close()
        return {**summary(self.samples), "errors": len(self.errors),
                "error_types": sorted(set(self.errors)), "stopped": not self.thread.is_alive(),
                "while_job_active": summary(self.active_samples)}


def poll(client, status, status_url, timeout):
    began = time.perf_counter()
    result = {"initial_status": status, "transitions": [status], "polls": 0}
    durations = []
    while status in ACTIVE:
        if not status_url or not STATUS_PATH.fullmatch(status_url):
            raise ValueError("An active job must expose its status-only endpoint")
        if time.perf_counter() - began >= timeout:
            result["final_status"] = "timeout"
            break
        time.sleep(1)
        response, elapsed = request(client, status_url)
        durations.append(elapsed)
        state = response.json()
        status = state.get("status")
        if status not in ACTIVE | TERMINAL:
            raise ValueError("Unknown prediction state")
        if state.get("terminal") != (status in TERMINAL):
            raise ValueError("Inconsistent prediction terminal state")
        if status != result["transitions"][-1]:
            result["transitions"].append(status)
        result["polls"] += 1
    result.setdefault("final_status", status)
    result["wait_seconds"] = time.perf_counter() - began
    result["status_http"] = summary(durations)
    return result


def page_case(client, method, horizon, timeout):
    path = "/predictive/demand?" + urlencode({"method": method, "horizon": horizon})
    began = time.perf_counter()
    probe = Responsiveness(client)
    probe.thread.start()
    try:
        response, first_seconds = request(client, path)
        first = PredictionMarkup(response.text)
        if first.status in ACTIVE:
            probe.active.set()
        result = poll(client, first.status, first.status_url, timeout)
        probe.active.clear()
        result.update({"method": method, "horizon": horizon, "first_http_seconds": first_seconds})
        if result["final_status"] in ("ready", "unavailable"):
            response, final_seconds = request(client, path)
            final = PredictionMarkup(response.text)
            result.update({"rendered_status": final.status, "final_http_seconds": final_seconds,
                           "pending_banner": "Pronóstico pendiente" in response.text,
                           "passed": final.status == result["final_status"] and final.status not in ACTIVE
                           and "Pronóstico pendiente" not in response.text})
        else:
            result["passed"] = False
    finally:
        load = probe.finish()
    result["lightweight_during_case"] = load
    result["total_seconds"] = time.perf_counter() - began
    result["passed"] = result["passed"] and load["errors"] == 0 and load["stopped"]
    return result


def detail_case(client, product_id, timeout):
    path = f"/api/predictions/products/{product_id}/detail"
    response, first_seconds = request(client, path)
    view = response.json()
    result = poll(client, view["computation_status"], view.get("computation_status_url"), timeout)
    result.update({"product_id": product_id, "method": "auto", "first_http_seconds": first_seconds})
    if result["final_status"] in ("ready", "unavailable"):
        response, elapsed = request(client, path)
        view = response.json()
        periods = view["periods"]
        result.update({"rendered_status": view["computation_status"], "final_http_seconds": elapsed,
                       "periods": [{"days": p["days"], "path_count": len(p["path"]),
                                    "total": p["total"]} for p in periods],
                       "passed": view["computation_status"] == result["final_status"]
                       and sorted(p["days"] for p in periods) == list(HORIZONS)})
    else:
        result["passed"] = False
    return result


def authorized_client():
    # Guard environment and actual connected DB before importing/creating Flask.
    if validate_demo() != "conejo_demo_8b867d4b":
        raise RuntimeError("Only the explicitly authorized DEMO database is allowed")
    from docker_support.bootstrap import engine, identity
    from docker_support.demo import require_ready

    target = engine()
    try:
        validate_demo_url(target.url)
        with target.connect() as connection:
            if not identity(connection).ready:
                raise RuntimeError("DEMO installation is not ready")
            require_ready(connection)
            if connection.execute(text("SELECT DATABASE()")).scalar_one() != "conejo_demo_8b867d4b":
                raise RuntimeError("Actual database mismatch")
    finally:
        target.dispose()
    from app import create_app
    from app.extensions import db
    from app.models import User
    from app.services import session_service
    from flask import session

    app = create_app()
    with app.test_request_context(base_url=BASE):
        validate_demo_url(db.engine.url)
        user = User.query.filter_by(role="admin", is_active=True).order_by(User.id).first()
        if user is None:
            raise RuntimeError("An existing active DEMO administrator is required")
        session_service.open_session(user)
        cookie = app.session_interface.get_signing_serializer(app).dumps(dict(session))
    client = requests.Session()
    client.trust_env = False
    client.cookies.set(app.config["SESSION_COOKIE_NAME"], cookie)
    return client


def run(args):
    output = Path(args.output).resolve()
    if os.name != "posix" or not output.is_relative_to(Path("/tmp")) or output.suffix != ".json":
        raise ValueError("Report must be a JSON file strictly beneath container /tmp")
    if output.exists():
        raise ValueError("Existing validation evidence will not be replaced")
    report = {"phase": args.mode, "pages": [], "details": [], "complete": False,
              "phase_prepared_externally": True, "http_port": 5001,
              "detail_contract": "auto simultaneously for H7/H30/H90"}

    def save():
        output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")

    client = authorized_client()
    try:
        idle = [request(client, "/api/auth/me")[1] for _ in range(5)]
        report["lightweight_idle"] = summary(idle)
        save()
        for method in args.methods:
            for horizon in HORIZONS:
                try:
                    result = page_case(client, method, horizon, args.timeout)
                except (requests.RequestException, RuntimeError, ValueError, KeyError) as error:
                    result = {"method": method, "horizon": horizon, "passed": False,
                              "error_type": type(error).__name__}
                report["pages"].append(result)
                save()
                print(json.dumps({"method": method, "horizon": horizon,
                                  "passed": result["passed"]}), flush=True)
        for product_id in args.product_ids:
            try:
                result = detail_case(client, product_id, args.timeout)
            except (requests.RequestException, RuntimeError, ValueError, KeyError) as error:
                result = {"product_id": product_id, "passed": False, "error_type": type(error).__name__}
            report["details"].append(result)
            save()
        report["complete"] = True
        report["passed"] = all(row["passed"] for row in report["pages"] + report["details"])
        report["pending_timeouts"] = sum(row.get("final_status") == "timeout"
                                         for row in report["pages"] + report["details"])
        save()
        return 0 if report["passed"] else 1
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("cold", "hot", "restart"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS))
    parser.add_argument("--product-ids", nargs="+", type=int, default=[1])
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 1800 or any(value < 1 for value in args.product_ids):
        parser.error("Timeout must be 1..1800 and product IDs positive")
    return run(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Never print exception messages, response bodies, URLs, sessions or SQL.
        print(json.dumps({"failed": True, "error_type": type(error).__name__}), flush=True)
        raise SystemExit(1) from None
