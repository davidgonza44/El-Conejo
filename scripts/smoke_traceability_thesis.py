"""Temporary loopback HTTP smoke against thesis with database writes prohibited.

An ephemeral header authenticates an existing active admin without creating a
persisted login/session. Only the session persistence availability hook is mocked;
document permissions, vendor scopes and password policy remain authoritative.
No application files/configuration or business rows are changed.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import threading
from unittest.mock import patch
from urllib.request import Request, urlopen

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from sqlalchemy import event, text
from werkzeug.serving import make_server, WSGIRequestHandler
from audit_traceability_thesis import BACKUPS, DATABASE, engine_for, protect_readonly, save_json
from app import create_app
from app.extensions import db, login_manager
from app.models import User


class QuietHandler(WSGIRequestHandler):
    def log(self, _type, _message, *_args):
        pass


def application():
    engine = engine_for(DATABASE)

    class Config:
        SECRET_KEY = secrets.token_urlsafe(32)
        SQLALCHEMY_DATABASE_URI = engine.url.render_as_string(hide_password=False)
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        DEBUG = False
        TESTING = False
        MAIL_ENABLED = False

    engine.dispose()
    app = create_app(Config)
    app.logger.disabled = True  # Never print real row values on a smoke failure.
    with app.app_context():
        protect_readonly(db.engine)
        assert db.session.execute(text("SELECT DATABASE()")).scalar_one() == DATABASE
        assert db.session.execute(text("SELECT @@tx_read_only")).scalar_one() == 1
        admin_id = db.session.query(User.id).filter_by(role="admin", is_active=True).order_by(User.id).first()
        if admin_id is None:
            raise RuntimeError("No active admin available for authorized read-only smoke.")
        admin_id = admin_id[0]
    token = secrets.token_urlsafe(32)

    @login_manager.request_loader
    def ephemeral_user(request):
        if secrets.compare_digest(request.headers.get("X-Read-Smoke", ""), token):
            return db.session.get(User, admin_id)
        return None

    def restrict_request():
        from flask import request
        if request.method != "GET" or not secrets.compare_digest(request.headers.get("X-Read-Smoke", ""), token):
            return "Read-only smoke access denied", 403

    app.before_request_funcs[None].insert(0, restrict_request)
    return app, token


def sample_ids(app):
    sql = {
        "sale": "SELECT stock_movement_id FROM sale_lines WHERE stock_movement_id IS NOT NULL ORDER BY id DESC LIMIT 1",
        "purchase_reception": "SELECT stock_movement_id FROM purchase_reception_lines WHERE stock_movement_id IS NOT NULL ORDER BY id DESC LIMIT 1",
        "unknown": "SELECT m.id FROM stock_movements m WHERE NOT EXISTS (SELECT 1 FROM sale_lines s WHERE s.stock_movement_id=m.id) "
                   "AND NOT EXISTS (SELECT 1 FROM purchase_reception_lines p WHERE p.stock_movement_id=m.id) "
                   "AND NOT EXISTS (SELECT 1 FROM reconciliation_lines r WHERE r.stock_movement_id=m.id) ORDER BY m.id DESC LIMIT 1",
    }
    with app.app_context():
        samples = {kind: db.session.execute(text(query)).scalar() for kind, query in sql.items()}
        assert all(samples.values()), "Required real smoke origin sample is absent"
    return samples


def verify_http(base, token, samples):
    checks = []

    def get(path, *, as_json=False):
        request = Request(base + path, headers={"X-Read-Smoke": token})
        with urlopen(request, timeout=30) as response:
            assert response.status == 200 and response.url == base + path, "Unexpected HTTP status/redirect"
            data = response.read()
            checks.append({"path": path, "status": response.status})
            return json.loads(data) if as_json else data

    page = get("/inventory")
    assert b"ix_movements_live" in page
    listing = get("/api/inventory/movements?per_page=100", as_json=True)
    assert listing["items"] and listing["total"] >= len(listing["items"])
    for kind, movement_id in samples.items():
        row = get(f"/api/inventory/movements/{movement_id}", as_json=True)
        assert row["creation_origin"] is None and row["external_reference"] is None
        assert row["movement_number"] == f"MOV-{movement_id:06d}"
        if kind == "unknown":
            assert row["source_type"] is None and row["source_url"] is None
            assert row["source_label"] == "Origen no identificado"
            assert row["display_reference"] == row["movement_number"]
        else:
            assert row["source_type"] == kind and row["source_url"]
            get(row["source_url"])
            if kind == "sale":
                assert row["display_reference"].startswith("VTA-")
                assert row["source_url"] == "/sales/" + row["display_reference"]
            else:
                assert row["display_reference"].startswith("OC-")
                assert row["display_reference"].endswith(f"REC-{row['source_id']:05d}")
                reception = get(f"/api/purchases/receptions/{row['source_id']}", as_json=True)
                linked = next(item for item in reception["movements"] if item["id"] == movement_id)
                assert linked["display_reference"] == row["display_reference"]
        matches = [item for item in listing["items"] if item["id"] == movement_id]
        for match in matches:
            assert match["display_reference"] == row["display_reference"]
    for row in get("/api/inventory/movements/summary", as_json=True)["recent"]:
        detail = get(f"/api/inventory/movements/{row['id']}", as_json=True)
        assert row["display_reference"] == detail["display_reference"]
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-directory", required=True, type=Path)
    directory = parser.parse_args().run_directory.resolve()
    if directory.parent != BACKUPS.resolve() or not (directory / "POST.json").is_file():
        raise RuntimeError("Successful migration evidence directory required.")
    app, token = application()
    queries = []
    with app.app_context():
        @event.listens_for(db.engine, "before_cursor_execute")
        def count_reads(_connection, _cursor, statement, _parameters, _context, _many):
            queries.append(statement.lstrip().split(None, 1)[0].upper())

    server = make_server("127.0.0.1", 0, app, request_handler=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch("app.services.session_service.is_available", return_value=False), \
                patch("app.services.notification_providers.send", side_effect=AssertionError("External delivery prohibited")):
            checks = verify_http("http://127.0.0.1:" + str(server.server_port), token, sample_ids(app))
        assert set(queries) <= {"SELECT", "SHOW", "DESCRIBE"}
        result = {"database": DATABASE, "http_checks": checks, "sql_reads": len(queries), "sql_writes": 0,
                  "authentication": "ephemeral existing admin; no persisted login/session; RBAC unchanged",
                  "references": ["sale VTA", "purchase exact OC + REC", "unknown MOV"],
                  "reconciliation": "No real runs; CONC verified only in isolated suites",
                  "database_read_only": True, "server_stopped_after_test": True}
        save_json(directory / "SMOKE.json", result)
        print(json.dumps(result))
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


if __name__ == "__main__":
    main()
