#!/usr/bin/env python3
"""Isolated Products history suite: temporary SQLite, no MySQL, no ferreteria_conejo.

Covers period/comparison math (previous, year, Feb 29), Caracas boundary, first /
repeated / next-day snapshots, transitions captured only on the next close, live
today vs snapshot past, zero denominator, insufficient history, validation, roles.
Run: python -B scripts/test_product_history.py
"""
from __future__ import annotations

import os
import secrets
import sys
import tempfile
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from flask_login import current_user, login_user  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Category, InventorySnapshot, Product, User  # noqa: E402
from app.services import inventory_service, inventory_snapshot_service as snaps, product_history_service as hist  # noqa: E402
from app.services import product_service, rbac_service
from app.models.product_change import ProductChange  # noqa: E402

PASSWORD = "IsolatedHistory123!"


class IsolatedConfig:
    SECRET_KEY = secrets.token_urlsafe(32)
    APP_ENV = "testing"
    TESTING = True
    WTF_CSRF_ENABLED = False
    MAIL_ENABLED = False
    GOOGLE_CLIENT_ID = GOOGLE_CLIENT_SECRET = GOOGLE_REDIRECT_URI = None
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = ""


def test_math():
    assert hist.previous_range(date(2026, 9, 1), date(2026, 9, 30)) == (date(2026, 8, 2), date(2026, 8, 31))
    assert hist.previous_range(date(2026, 9, 1), date(2026, 9, 11)) == (date(2026, 8, 21), date(2026, 8, 31))
    assert hist.previous_range(date(2026, 9, 11), date(2026, 9, 11)) == (date(2026, 9, 10), date(2026, 9, 10))
    assert hist.year_range(date(2024, 2, 29), date(2024, 2, 29)) == (date(2023, 2, 28), date(2023, 2, 28))
    assert hist.year_range(date(2024, 2, 1), date(2024, 3, 31)) == (date(2023, 2, 1), date(2023, 3, 31))
    assert hist.shift_year(date(2025, 3, 1)) == date(2024, 3, 1)
    # 03:30 UTC is still the previous Caracas day; 04:30 UTC is the new one.
    assert snaps.commercial_today(datetime(2026, 9, 11, 3, 30, tzinfo=timezone.utc)) == date(2026, 9, 10)
    assert snaps.commercial_today(datetime(2026, 9, 11, 4, 30, tzinfo=timezone.utc)) == date(2026, 9, 11)
    assert hist._kpi("low_stock", 5, 0) == {"value": 5, "comparison_value": 0, "delta": 5, "change_percent": None, "sentiment": "negative"}
    assert hist._kpi("total_products", 0, 0)["change_percent"] is None and hist._kpi("total_products", 0, 0)["sentiment"] == "neutral"
    assert hist._kpi("total_products", 3, 4)["sentiment"] == "neutral" and hist._kpi("total_products", 4, 3)["sentiment"] == "positive"
    assert hist._kpi("low_stock", 3, 4)["sentiment"] == "positive" and hist._kpi("out_of_stock", 4, 3)["change_percent"] == 33.3
    assert hist._kpi("active_categories", None, 3)["delta"] is None
    print("PASS math: previous/year ranges, Feb 29 clamp, Caracas boundary, delta/percent/sentiment rules")


def approved_product_change(operation, *args):
    """Use the real proposal/approval services with distinct authorized actors."""
    if operation == "create_product":
        # History fixtures use zero synthetic prices, explicitly denominated in USD.
        args = ({"sale_price": "0.00", "sale_price_currency": "USD",
                 "purchase_price": "0.00", "purchase_price_currency": "USD", **args[0]},)
    previous = db.session.get(User, int(current_user.id))
    assistant = User.query.filter_by(username="hist_asistente").one()
    admin = User.query.filter_by(username="hist_admin").one()
    try:
        login_user(assistant)
        result = getattr(product_service, operation)(*args)
        product_id = result.id if operation == "create_product" else args[0]
        proposal_id = None if operation == "create_product" else ProductChange.query.filter_by(
            product_id=product_id, status="pending").one().id
        login_user(admin)
        return product_service.decide_product(product_id, approve=True, proposal_id=proposal_id)
    finally:
        login_user(previous)


def setup(temp_root, cleanup):
    IsolatedConfig.SQLALCHEMY_DATABASE_URI = "sqlite:///" + (temp_root / "history.sqlite").as_posix()
    app = create_app(IsolatedConfig)
    cleanup.callback(dispose_app, app)
    with app.app_context():
        db.create_all()
        rbac_service.ensure_seeded()
        users = {}
        for role in ("admin", "asistente", "inventario", "vendedor"):
            user = User(name=role, email=f"{role}@hist.test", username=f"hist_{role}", role=role)
            user.set_password(PASSWORD)
            users[role] = user
        cat_a, cat_b = Category(name="Hist A"), Category(name="Hist B")
        db.session.add_all([*users.values(), cat_a, cat_b])
        db.session.commit()
        with app.test_request_context():
            login_user(users["admin"])
            products = {
                "steady": approved_product_change('create_product', {"code": "H-1", "name": "Estable", "category_id": cat_a.id, "current_stock": 50, "minimum_stock": 10}),
                "to_low": approved_product_change('create_product', {"code": "H-2", "name": "Bajará", "category_id": cat_a.id, "current_stock": 40, "minimum_stock": 10}),
                "to_out": approved_product_change('create_product', {"code": "H-3", "name": "Se agotará", "category_id": cat_b.id, "current_stock": 5, "minimum_stock": 10}),
                "to_inactive": approved_product_change('create_product', {"code": "H-4", "name": "Se desactivará", "category_id": cat_b.id, "current_stock": 20, "minimum_stock": 5}),
            }
            ids = {key: product.id for key, product in products.items()}
        cat_b_id = cat_b.id
    clients = {}
    for role in users:
        client = app.test_client()
        assert client.post("/api/auth/login", json={"identifier": f"hist_{role}", "password": PASSWORD}).status_code == 200
        clients[role] = client
    return app, clients, ids, cat_b_id


def get(client, **params):
    response = client.get("/api/products/history/summary", query_string=params)
    return response.status_code, response.json


def dispose_app(app):
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def main():
    test_math()
    with tempfile.TemporaryDirectory() as temp, ExitStack() as cleanup:
        app, clients, ids, cat_b = setup(Path(temp), cleanup)
        admin = clients["admin"]
        # Disposable clock simulation: the daily service now rejects late captures.
        now = datetime.combine(snaps.commercial_today(), snaps.CLOSING_TIME, inventory_service.COMMERCIAL_ZONE)
        today = snaps.commercial_today(now)
        d1, d2, d4 = today - timedelta(days=1), today - timedelta(days=2), today - timedelta(days=4)
        iso = lambda day: day.isoformat()
        values = lambda body: {key: kpi["value"] for key, kpi in body["kpis"].items()}

        # No snapshots yet: today is live, everything else is honestly unavailable.
        status, body = get(admin, start=iso(today), end=iso(today), compare="previous")
        live = admin.get("/api/products/summary").json
        assert status == 200 and body["history"]["reason"] == "comparison_history_not_started"
        assert body["history"]["available_from"] is None and body["period"]["available"] and not body["comparison"]["available"]
        assert values(body) == {"total_products": live["total_active"], "low_stock": live["low_stock"], "out_of_stock": live["out_of_stock"], "active_categories": live["active_categories"]}
        assert values(body) == {"total_products": 4, "low_stock": 1, "out_of_stock": 0, "active_categories": 2}
        assert all(kpi["delta"] is None and kpi["change_percent"] is None for kpi in body["kpis"].values())
        status, body = get(admin, start=iso(d1), end=iso(d1), compare="none")
        assert body["history"]["reason"] == "period_history_not_started" and set(values(body).values()) == {None}
        print("PASS insufficient history: no snapshots -> live today, null past, explicit reasons, no zeros")

        with app.app_context():
            first = snaps.take_snapshot(now - timedelta(days=3))            # writes d4
            assert first == {"snapshot_date": iso(d4), "inserted": 4, "existing": 0, "skipped": False}
            again = snaps.take_snapshot(now - timedelta(days=3))
            assert again["skipped"] and again["existing"] == 4 and InventorySnapshot.query.filter_by(snapshot_date=d4).count() == 4
            snaps.take_snapshot(now - timedelta(days=1))                    # writes d2
            with app.test_request_context():
                login_user(User.query.filter_by(username="hist_inventario").one())
                inventory_service.register_exit({"product_id": ids["to_low"], "quantity": 32})      # 8 <= 10 -> low
                inventory_service.register_exit({"product_id": ids["to_out"], "quantity": 5})       # 0 -> out
                # Solo se desactiva sin existencias: primero sale el stock restante.
                inventory_service.register_exit({"product_id": ids["to_inactive"], "quantity": 20})
                approved_product_change('deactivate_product', ids["to_inactive"])
                approved_product_change('update_product', ids["steady"], {"minimum_stock": 60})               # 50 <= 60 -> low
                approved_product_change('create_product', {"code": "H-5", "name": "Nuevo", "category_id": cat_b, "current_stock": 3, "minimum_stock": 1})
            # Live changed, but the already-captured close d2 must not move.
            assert hist.snapshot_kpis(d2) == {"total_products": 4, "low_stock": 1, "out_of_stock": 0, "active_categories": 2}
            yesterday = snaps.take_snapshot(now)                                                   # writes d1
            assert yesterday["snapshot_date"] == iso(d1) and yesterday["inserted"] == 5
            assert hist.snapshot_kpis(d1) == {"total_products": 4, "low_stock": 2, "out_of_stock": 1, "active_categories": 2}
            assert hist.snapshot_kpis(today - timedelta(days=3)) is None
        print("PASS snapshots: first/repeated/next-day, inactive rows kept, transitions only on the next close")

        status, body = get(admin, start=iso(d1), end=iso(d1), compare="previous")
        assert body["history"]["reason"] == "ok" and body["comparison"] == {"type": "previous", "start": iso(d2), "end": iso(d2), "available": True}
        assert body["kpis"]["total_products"] == {"value": 4, "comparison_value": 4, "delta": 0, "change_percent": 0.0, "sentiment": "neutral"}
        assert body["kpis"]["low_stock"] == {"value": 2, "comparison_value": 1, "delta": 1, "change_percent": 100.0, "sentiment": "negative"}
        assert body["kpis"]["out_of_stock"] == {"value": 1, "comparison_value": 0, "delta": 1, "change_percent": None, "sentiment": "negative"}
        assert body["kpis"]["active_categories"]["delta"] == 0
        print("PASS previous period + zero denominator: exact deltas, null percent instead of infinity")

        status, body = get(admin, start=iso(today), end=iso(today + timedelta(days=5)), compare="previous")
        live = admin.get("/api/products/summary").json
        assert body["period"]["clamped"] and body["period"]["end"] == iso(today)
        assert values(body) == {"total_products": live["total_active"], "low_stock": live["low_stock"], "out_of_stock": live["out_of_stock"], "active_categories": live["active_categories"]}
        assert values(body) == {"total_products": 4, "low_stock": 2, "out_of_stock": 1, "active_categories": 2}
        assert body["kpis"]["low_stock"]["comparison_value"] == 2 and body["comparison"]["end"] == iso(d1)
        print("PASS today uses live state (clamped future end), yesterday uses the stored close")

        status, body = get(admin, start=iso(d1), end=iso(d1), compare="year")
        assert body["history"]["reason"] == "comparison_before_history" and not body["comparison"]["available"]
        assert body["comparison"]["end"] == iso(hist.shift_year(d1)) and body["kpis"]["low_stock"]["value"] == 2
        status, body = get(admin, start=iso(d4 - timedelta(days=10)), end=iso(d4 - timedelta(days=5)), compare="none")
        assert body["history"]["reason"] == "period_before_history" and body["history"]["available_from"] == iso(d4)
        status, body = get(admin, start=iso(d4), end=iso(today - timedelta(days=3)), compare="none")
        assert body["history"]["reason"] == "period_snapshot_missing" and values(body)["total_products"] is None
        status, body = get(admin, start=iso(d1), end=iso(d1), compare="none")
        assert body["history"]["reason"] == "no_comparison" and body["comparison"]["start"] is None
        print("PASS reasons: comparison_before_history, period_before_history, period_snapshot_missing, no_comparison")

        for params in ({"start": iso(d1), "end": iso(d2)}, {"start": "ayer", "end": iso(d1)}, {"start": iso(d1)},
                       {"start": iso(d1), "end": iso(d1), "compare": "trimestre"}, {"start": iso(today + timedelta(days=1)), "end": iso(today + timedelta(days=2))}):
            assert get(admin, **params)[0] == 400, params
        assert app.test_client().get("/api/products/history/summary", query_string={"start": iso(d1), "end": iso(d1)}).status_code == 401
        assert get(clients["vendedor"], start=iso(d1), end=iso(d1))[0] == 200
        print("PASS validation 400s, anonymous 401, vendedor read allowed")
    print("PASS: isolated Products history suite")


if __name__ == "__main__":
    main()
