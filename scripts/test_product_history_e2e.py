"""Products history E2E against the disposable ferreteria_conejo_e2e database ONLY.

Migration (real, idempotent), production snapshot job, deterministic seed (refusal of the
valuable DB, idempotency, own-rows cleanup), exact KPI windows, live today, reference mode.
Never loads dotenv or connects to ferreteria_conejo. Leaves the seeded history in place for
browser validation. Run: python -B scripts/test_product_history_e2e.py
"""
import os
import secrets
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pymysql
from sqlalchemy import text

import seed_product_history_e2e as seed
from migrate_inventory_snapshots import TABLE, migrate
from serve_inventory_e2e import E2EConfig
from app import create_app
from app.extensions import db
from app.models import InventorySnapshot, Product, User
from app.services import inventory_snapshot_service as snaps

DATABASE = "ferreteria_conejo_e2e"
PASSWORD = "DisposableHistoryOnly123!"
SEED = [sys.executable, "-B", str(ROOT / "scripts" / "seed_product_history_e2e.py")]


def run(*args):
    return subprocess.run([*SEED, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT)


def migration_checks():
    connection = pymysql.connect(host="127.0.0.1", user="root", password="", database=DATABASE, charset="utf8mb4")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE()")
            assert cursor.fetchone()[0] == DATABASE
            cursor.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (TABLE,))
            first = cursor.fetchone()[0] == 0
        assert migrate(connection, DATABASE) is first
        assert migrate(connection, DATABASE) is False
        with connection.cursor() as cursor:
            cursor.execute("SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
                           "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (TABLE,))
            columns = cursor.fetchall()
            assert [c[0] for c in columns] == ["id", "snapshot_date", "product_id", "stock", "minimum_stock", "product_is_active",
                                               "category_id", "category_is_active", "created_at"]
            assert all(c[2] == "NO" for c in columns) and dict((c[0], c[1]) for c in columns)["snapshot_date"] == "date"
            cursor.execute("SELECT NON_UNIQUE, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) FROM information_schema.STATISTICS "
                           "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME='uq_inventory_snapshots_date_product'", (TABLE,))
            assert cursor.fetchone() == (0, "snapshot_date,product_id")
            cursor.execute("SELECT REFERENCED_TABLE_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=DATABASE() "
                           "AND TABLE_NAME=%s AND REFERENCED_TABLE_NAME IS NOT NULL", (TABLE,))
            assert [r[0] for r in cursor.fetchall()] == ["products"], "only product_id carries a FK"
    finally:
        connection.close()
    print("PASS migration: real create on the disposable DB, second run strict no-op, schema/unique/FK as designed")


def refusal_checks():
    for args in (("--database", "ferreteria_conejo"), ("--database", "ferreteria_conejo", "--cleanup"), ("--database", "inventory_database")):
        result = run(*args)
        assert result.returncode != 0 and "REFUSED" in (result.stderr + result.stdout), (args, result.stdout, result.stderr)
    assert run().returncode != 0, "--database must be explicit"
    print("PASS seed refuses ferreteria_conejo (and any non-E2E name) before connecting; --database is mandatory")


def snapshot_job_checks(app):
    with app.app_context():
        yesterday = snaps.commercial_today() - timedelta(days=1)
        products = Product.query.count()
        result = snaps.take_snapshot()
        stored = InventorySnapshot.query.filter_by(snapshot_date=yesterday).count()
        if result["skipped"]:
            assert result["existing"] == stored > 0
        else:
            assert result == {"snapshot_date": yesterday.isoformat(), "inserted": products, "existing": 0, "skipped": False}
            assert stored == products
        again = snaps.take_snapshot()
        assert again["skipped"] and InventorySnapshot.query.filter_by(snapshot_date=yesterday).count() == stored
        assert db.session.query(InventorySnapshot.snapshot_date).filter(InventorySnapshot.snapshot_date >= yesterday).distinct().count() == 1
        assert InventorySnapshot.query.filter(InventorySnapshot.snapshot_date > yesterday).count() == 0, "never writes today or the future"
    print(f"PASS production snapshot job on E2E: yesterday {yesterday} captured for every product once; repeat is a no-op")


def own_snapshot_rows(app):
    with app.app_context():
        ids = [p.id for p in Product.query.filter(Product.code.in_(seed.PRODUCTS)).all()]
        rows = db.session.query(InventorySnapshot.snapshot_date, Product.code, InventorySnapshot.stock, InventorySnapshot.minimum_stock,
                                InventorySnapshot.product_is_active, InventorySnapshot.category_id, InventorySnapshot.category_is_active) \
            .join(Product, Product.id == InventorySnapshot.product_id).filter(InventorySnapshot.product_id.in_(ids)).all() if ids else []
        total = InventorySnapshot.query.count()
        return sorted(rows), total, len(ids)


def seed_checks(app):
    first = run("--database", DATABASE)
    assert first.returncode == 0, first.stderr
    rows1, total1, count1 = own_snapshot_rows(app)
    second = run("--database", DATABASE)
    assert second.returncode == 0, second.stderr
    rows2, total2, count2 = own_snapshot_rows(app)
    assert rows1 == rows2 and total1 == total2 and count1 == count2 == len(seed.PRODUCTS), "seed must be idempotent"
    yesterday = snaps.commercial_today() - timedelta(days=1)
    expected_rows = sum(1 for code in seed.PRODUCTS for offset in range((yesterday - seed.SEED_START).days + 1)
                        if seed.state_at(code, seed.SEED_START + timedelta(days=offset)))
    assert len(rows1) == expected_rows
    assert {row[0] for row in rows1} == {seed.SEED_START + timedelta(days=n) for n in range((yesterday - seed.SEED_START).days + 1)}
    assert not any(row[0] < seed.SEED_START or row[0] > yesterday for row in rows1), "no dates outside the fixture range"
    with app.app_context():
        assert db.session.execute(text("SELECT COUNT(*) FROM stock_movements sm JOIN products p ON p.id=sm.product_id WHERE p.code LIKE 'E2E-HIST-%'")).scalar() == 0
    print(f"PASS seed: deterministic and idempotent ({len(rows1)} own closes {seed.SEED_START}..{yesterday}), no synthetic StockMovement")


def cleanup_checks(app):
    with app.app_context():
        others_before = db.session.execute(text("SELECT COUNT(*) FROM inventory_snapshots s JOIN products p ON p.id=s.product_id WHERE p.code NOT LIKE 'E2E-HIST-%'")).scalar()
        products_before = db.session.execute(text("SELECT COUNT(*) FROM products WHERE code NOT LIKE 'E2E-HIST-%'")).scalar()
        categories_before = db.session.execute(text("SELECT COUNT(*) FROM categories WHERE name NOT LIKE 'E2E-HIST%'")).scalar()
    result = run("--database", DATABASE, "--cleanup")
    assert result.returncode == 0, result.stderr
    rows, _, count = own_snapshot_rows(app)
    with app.app_context():
        assert rows == [] and count == 0
        assert db.session.execute(text("SELECT COUNT(*) FROM categories WHERE name LIKE 'E2E-HIST%'")).scalar() == 0
        assert db.session.execute(text("SELECT COUNT(*) FROM inventory_snapshots s JOIN products p ON p.id=s.product_id WHERE p.code NOT LIKE 'E2E-HIST-%'")).scalar() == others_before
        assert db.session.execute(text("SELECT COUNT(*) FROM products WHERE code NOT LIKE 'E2E-HIST-%'")).scalar() == products_before
        assert db.session.execute(text("SELECT COUNT(*) FROM categories WHERE name NOT LIKE 'E2E-HIST%'")).scalar() == categories_before
    assert run("--database", DATABASE).returncode == 0
    print("PASS cleanup removes only E2E-HIST rows; other snapshots/products/categories untouched; reseeded for browser validation")


def expected_kpis(window):
    current, previous = seed.EXPECTED[window["current"]], seed.EXPECTED[window["previous"]]
    result = {}
    for key in current:
        delta = current[key] - previous[key]
        up = {"total_products": "positive", "low_stock": "negative", "out_of_stock": "negative", "active_categories": "positive"}
        down = {"total_products": "neutral", "low_stock": "positive", "out_of_stock": "positive", "active_categories": "neutral"}
        result[key] = {"value": current[key], "comparison_value": previous[key], "delta": delta,
                       "change_percent": round(delta * 100 / previous[key], 1) if previous[key] else None,
                       "sentiment": "neutral" if delta == 0 else (up if delta > 0 else down)[key]}
    return result


def api_checks(app):
    assert snaps.commercial_today() > date(2026, 9, 9), "exact windows are snapshot-based only once 2026-09-09 is in the past"
    run_id = secrets.token_hex(4)
    with app.app_context():
        user = User(name="Historial E2E", username="hist_" + run_id, email=f"hist_{run_id}@example.invalid", role="admin")
        user.set_password(PASSWORD)
        db.session.add(user)
        db.session.commit()
    client = app.test_client()
    assert client.post("/api/auth/login", json={"identifier": "hist_" + run_id, "password": PASSWORD}).status_code == 200
    for name, window in seed.WINDOWS.items():
        response = client.get("/api/products/history/summary", query_string={k: window[k] for k in ("start", "end", "compare")})
        body = response.json
        assert response.status_code == 200, body
        assert body["history"]["reason"] == "ok" and body["history"]["available_from"] == seed.SEED_START.isoformat()
        assert body["period"]["end"] == window["end"] and not body["period"]["clamped"]
        assert (body["comparison"]["start"], body["comparison"]["end"], body["comparison"]["available"]) == (*window["comparison"], True)
        assert body["kpis"] == expected_kpis(window), (name, body["kpis"])
        print(f"PASS window {name} {window['start']}..{window['end']} compare={window['compare']}: "
              + ", ".join(f"{k}={v['value']} vs {v['comparison_value']} ({v['delta']:+d}, {v['change_percent']})" for k, v in body["kpis"].items()))
    a = expected_kpis(seed.WINDOWS["A"])
    assert a["low_stock"]["change_percent"] is None and a["low_stock"]["delta"] == 2, "previous 0 -> current 2 shows Nuevo, never infinity"
    assert expected_kpis(seed.WINDOWS["B"])["active_categories"]["delta"] == 0
    assert expected_kpis(seed.WINDOWS["YEAR"])["out_of_stock"] == {"value": 2, "comparison_value": 0, "delta": 2, "change_percent": None, "sentiment": "negative"}

    today = snaps.commercial_today().isoformat()
    live = client.get("/api/products/summary").json
    body = client.get("/api/products/history/summary", query_string={"start": today, "end": today, "compare": "previous"}).json
    assert {k: v["value"] for k, v in body["kpis"].items()} == {"total_products": live["total_active"], "low_stock": live["low_stock"],
                                                                "out_of_stock": live["out_of_stock"], "active_categories": live["active_categories"]}
    assert body["comparison"]["available"] and body["comparison"]["end"] == (snaps.commercial_today() - timedelta(days=1)).isoformat()
    print("PASS today = live /api/products/summary values; comparison uses yesterday's stored close")

    body = client.get("/api/products/history/summary", query_string={"start": "2024-01-01", "end": "2024-01-31", "compare": "previous"}).json
    assert body["history"] == {"available_from": seed.SEED_START.isoformat(), "current_period_available": False, "comparison_available": False,
                               "reason": "period_before_history"}
    assert all(v["value"] is None and v["delta"] is None for v in body["kpis"].values())
    print("PASS insufficient history before 2025-07-01: null values, explicit reason, no fabricated zeros")

    html = client.get("/products?ref=1").get_data(as_text=True)
    assert "1,247" in html and "vs. 01 abr. - 30 abr. 2024" in html and 'data-reference-date="2026-09-30"' in html
    assert "ix_products_live.js" not in html and 'data-commercial-today=""' in html and "ix-kpi-total-trend" not in html
    live_html = client.get("/products").get_data(as_text=True)
    assert "ix_products_live.js" in live_html and f'data-commercial-today="{today}"' in live_html and "ix-kpi-total-trend" in live_html
    assert "1,247" not in live_html
    print("PASS reference mode unchanged (fixture KPIs, no live script); live page carries commercial today + trend hooks")


if __name__ == "__main__":
    migration_checks()
    refusal_checks()
    app = create_app(E2EConfig)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        assert db.session.execute(text("SELECT DATABASE()")).scalar() == DATABASE
    snapshot_job_checks(app)
    seed_checks(app)
    cleanup_checks(app)
    api_checks(app)
    print("PASS: Products history E2E on ferreteria_conejo_e2e only")
