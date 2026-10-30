"""Synthetic MariaDB fixture. Never selects or copies an operational database."""
import json
import os
from pathlib import Path
import secrets
import sys
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, inspect, text
from app import create_app
from app.extensions import db
from app.models import Category, Product, User, StockMovement
from app.models.reconciliation import ReconciliationCut, ReconciliationRun
from app.services import inventory_service, rbac_service, reconciliation_automation_service as auto
from app.services import reconciliation_service as service, session_service
from app.utils.schema import reset_cache
from test_reconciliation_automatic_mariadb import _guard, _url
from migrate_reconciliation_cuts import validate_target

DAY = date(2031, 1, 15)
CSV = ("product_code;quantity;product_name\n"
       "SIM-IGUAL;12;SIMULADO Cemento coincidente\n"
       "SIM-FALTA;15;SIMULADO Disco faltante\n"
       "SIM-SOBRA;11;SIMULADO Cable sobrante\n"
       "SIM-DESCONOCIDO;4;SIMULADO Código ajeno\n").encode("utf-8")
SPECS = (("SIM-IGUAL", "SIMULADO Cemento", 12), ("SIM-FALTA", "SIMULADO Disco", 20),
         ("SIM-SOBRA", "SIMULADO Cable", 8))


class ControlledClock(datetime):
    instant = auto.scheduled_at(DAY, "0800") + timedelta(seconds=5)

    @classmethod
    def utcnow(cls):
        return cls.instant

    @classmethod
    def now(cls, tz=None):
        aware = cls.instant.replace(tzinfo=timezone.utc)
        return aware.astimezone(tz) if tz else cls.instant


def verify_server(connection):
    _guard()
    assert Path(connection.execute(text("SELECT @@datadir")).scalar()).resolve() == Path(os.environ["TEST_MARIADB_DATADIR"]).resolve()
    assert connection.execute(text("SELECT @@port")).scalar() == 3407
    assert connection.dialect.is_mariadb


def build_app(name=None):
    _guard()
    name = name or "reconciliation_test_" + secrets.token_hex(6)
    validate_target(name)
    server = create_engine(_url(), hide_parameters=True)
    with server.connect() as connection:
        verify_server(connection)
        assert connection.execute(text("SELECT DATABASE()")).scalar() is None
        exists = connection.execute(text("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=:n"), {"n": name}).scalar()
        if not exists:
            connection.execute(text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4"))
            connection.commit()
    server.dispose()

    class SyntheticConfig:
        TESTING = True
        APP_ENV = "testing"
        SECRET_KEY = "phase-e-public-synthetic-session-key"
        SQLALCHEMY_DATABASE_URI = _url(name)
        SQLALCHEMY_ENGINE_OPTIONS = {"isolation_level": "READ COMMITTED", "hide_parameters": True}
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        WTF_CSRF_ENABLED = False
        MAIL_ENABLED = False
        NOTIFICATIONS_DRY_RUN = True
        RECONCILIATION_ACTIVATED_ON = DAY.isoformat()
        RECONCILIATION_SETTLE_SECONDS = 0
        RECONCILIATION_LANDING_PATH = "landing"
        RECONCILIATION_INBOX_PATH = "inbox"
        RECONCILIATION_ARCHIVE_PATH = "archive"

    app = create_app(SyntheticConfig)
    app.instance_path = str(Path("/app/tmp") / name)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    with app.app_context():
        reset_cache()
        if not inspect(db.engine).has_table("products"):
            db.create_all()
            seed()
    return app, name


def seed():
    # Use the unchanged repository role definitions; no custom permissions.
    rbac_service.ensure_seeded()
    actors = [User(name=f"SIMULADO {role}", username=f"sim_{role}",
                   email=f"sim_{role}@example.invalid", role=role)
              for role in ("admin", "inventario", "vendedor", "soporte_ti")]
    category = Category(name="SIMULACIÓN FASE E")
    db.session.add_all([category, *actors])
    db.session.flush()
    for code, name, _ in SPECS:
        db.session.add(Product(code=code, name=name, category_id=category.id, current_stock=0,
                               purchase_price=1, sale_price=2, purchase_price_currency="USD", sale_price_currency="USD"))
    db.session.commit()
    from flask_login import login_user
    with _request_context():
        login_user(actors[0])
        for code, _, quantity in SPECS:
            product = Product.query.filter_by(code=code).one()
            inventory_service.register_entry(dict(product_id=product.id, quantity=quantity,
                reason="SIMULADO saldo inicial Fase E"), creation_origin="initial_stock")


def _request_context():
    from flask import current_app
    return current_app.test_request_context()


def clock(at):
    ControlledClock.instant = at
    return patch.object(auto, "datetime", ControlledClock)


def capture():
    with clock(auto.scheduled_at(DAY, "0800") + timedelta(seconds=5)):
        return auto.capture_cut(DAY, "0800")


def move_after():
    # Exactly one legitimate movement; replay leaves it intact.
    if StockMovement.query.filter_by(reason="SIMULADO movimiento posterior al corte").first():
        return
    from flask_login import login_user
    with _request_context():
        login_user(User.query.filter_by(role="admin").one())
        product = Product.query.filter_by(code="SIM-IGUAL").one()
        inventory_service.register_entry(dict(product_id=product.id, quantity=3,
            reason="SIMULADO movimiento posterior al corte"))


def publish(cut):
    from app.services.reconciliation_file_service import rename_new
    from flask import current_app
    path = Path(current_app.instance_path) / "landing" / auto.filename_for(cut)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        assert path.read_bytes() == CSV
    else:
        temporary = path.with_suffix(".csv.tmp")
        temporary.write_bytes(CSV)
        rename_new(temporary, path)
    return path


def reconcile(cut):
    with clock(auto.scheduled_at(DAY, "0800") + timedelta(minutes=15)):
        auto.ingest_due(ControlledClock.now(timezone.utc))
    db.session.expire_all()
    return db.session.get(ReconciliationCut, cut.id)


def state():
    cuts = ReconciliationCut.query.order_by(ReconciliationCut.id).all()
    return {"simulation": True, "day": DAY.isoformat(), "timezone": str(auto.CARACAS),
            "schedules": [value.strftime("%H:%M") for value in auto.TIMES.values()],
            "cuts": [dict(row.to_dict(), snapshot=auto.snapshot_detail(row)) for row in cuts],
            "stock": {product.code: product.current_stock for product in Product.query.order_by(Product.id)},
            "movements": [dict(id=m.id, code=db.session.get(Product, m.product_id).code,
                               previous=m.previous_stock, new=m.new_stock, quantity=m.quantity,
                               type=m.movement_type, reason=m.reason) for m in StockMovement.query.order_by(StockMovement.id)],
            "runs": [service.paged_detail(row.id) for row in ReconciliationRun.query.order_by(ReconciliationRun.id)]}


def assert_integrity():
    snapshot = state()
    expected = {code: quantity for code, _, quantity in SPECS}
    cut = ReconciliationCut.query.filter_by(scheduled_for=auto.scheduled_at(DAY, "0800")).one()
    assert {row.product_code_snapshot: row.quantity for row in cut.stock} == expected
    expected["SIM-IGUAL"] = 15
    assert snapshot["stock"] == expected
    assert len(snapshot["movements"]) == 4
    for product in Product.query:
        balance = 0
        for movement in StockMovement.query.filter_by(product_id=product.id).order_by(StockMovement.id):
            assert movement.previous_stock == balance
            assert abs(movement.new_stock - balance) == movement.quantity
            balance = movement.new_stock
        assert balance == product.current_stock
    return snapshot


def client_for(app, role):
    from flask import g, session
    client = app.test_client()
    with app.test_request_context():
        session_service.open_session(User.query.filter_by(role=role).one())
        g.pop("_login_user", None)
        cookies = dict(session)
    with client.session_transaction() as target:
        target.update(cookies)
    return client


def save_evidence(name):
    destination = Path("/app/tmp/phase-e-result.json")
    destination.write_text(json.dumps(dict(database=name, **assert_integrity()), ensure_ascii=False, indent=2), encoding="utf-8")
    return destination
