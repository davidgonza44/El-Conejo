"""Task-specific DEMO 5001 delta harness; never assumes a pristine seed.

Modes baseline/run/check. Reports contain synthetic IDs, counts and checks only.
Run creates valid HTTP transactions once; a started ledger refuses a second run.
No passwords are read or changed: dedicated test sessions use the session service.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from threading import Barrier
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import inspect, text

from docker_support.bootstrap import engine, identity
from docker_support.demo import require_ready
from docker_support.guard import validate_demo

BASE = "http://host.docker.internal:5001"
TABLES = ("users", "products", "sales", "sale_lines", "stock_movements", "purchase_orders",
          "purchase_receptions", "purchase_reception_lines", "customers", "suppliers",
          "historical_imports", "historical_demand_records", "inventory_adjustment_requests")
LEDGER = Path("/tmp/demo5001_closure.json")
BASELINE = Path("/tmp/demo5001_baseline.json")
REPORT = {"checks": []}
TARGET = None


def save():
    LEDGER.write_text(json.dumps(REPORT, indent=2, default=str), encoding="utf-8")


def check(condition, label):
    if not condition:
        raise AssertionError(label)
    REPORT["checks"].append(label)
    save()
    print("PASS", label, flush=True)


def rows(connection, sql, values=None):
    return [dict(row) for row in connection.execute(text(sql), values or {}).mappings()]


def snapshot():
    with TARGET.connect() as connection:
        require_ready(connection)
        assert connection.execute(text("SELECT DATABASE()")).scalar_one() == "conejo_demo_8b867d4b"
        tables = set(inspect(connection).get_table_names())
        result = {"revision": connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one(),
                  "counts": {t: connection.execute(text("SELECT COUNT(*) FROM " + t)).scalar_one() for t in TABLES if t in tables},
                  "users": rows(connection, "SELECT id,role,is_active FROM users ORDER BY id"),
                  "permissions": rows(connection, "SELECT r.slug,p.code FROM roles r JOIN role_permissions rp ON r.id=rp.role_id JOIN permissions p ON p.id=rp.permission_id ORDER BY r.slug,p.code"),
                  "stock": rows(connection, "SELECT id,code,current_stock FROM products ORDER BY id"),
                  "seed": rows(connection, "SELECT version,manifest,status FROM demo_seed"),
                  "sales_max": connection.execute(text("SELECT MAX(id) FROM sales")).scalar_one()}
        result["digests"] = {}
        # Synthetic commercial state only; never users, secrets, files or reports.
        for table in ("sales", "sale_lines", "stock_movements", "purchase_orders", "purchase_receptions",
                      "purchase_reception_lines", "inventory_adjustment_requests"):
            if table in tables:
                content = rows(connection, "SELECT * FROM " + table + " ORDER BY id")
                result["digests"][table] = hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()
        return result


def clients(app):
    from flask import session
    from app.extensions import db
    from app.models import User
    from app.services import session_service
    result = {}
    with app.app_context():
        ids = {user.role: user.id for user in User.query.all()}
    assert set(ids) == {"admin", "inventario", "vendedor", "soporte_ti"}
    for role, user_id in ids.items():
        with app.test_request_context(base_url=BASE):
            session_service.open_session(db.session.get(User, user_id))
            cookie = app.session_interface.get_signing_serializer(app).dumps(dict(session))
        client = requests.Session()
        client.trust_env = False
        client.cookies.set(app.config["SESSION_COOKIE_NAME"], cookie)
        result[role] = client
    return result


def call(client, path, body, expected, label):
    response = client.post(BASE + path, json=body, timeout=30)
    check(response.status_code == expected, label + " HTTP " + str(response.status_code))
    return response.json()


def business():
    state = snapshot()
    return state["counts"], state["stock"], state["digests"]


def sale_cases(clients):
    with TARGET.connect() as c:
        price = c.execute(text("SELECT sale_price FROM products WHERE id=14 AND code='DEMO-014'")).scalar_one()
        customer = c.execute(text("SELECT id FROM customers ORDER BY id LIMIT 1")).scalar_one()
    def payload(key, **changes):
        return {"customer_id": customer, "payment_method": "cash", "payment_condition": "cash",
                "cash_received": str(price), "items": [{"product_id": 14, "quantity": 1}],
                "idempotency_key": str(uuid5(NAMESPACE_URL, "demo5001-closure-20261004-" + key)), **changes}
    vendedor = clients["vendedor"]
    exact = call(vendedor, "/api/sales", payload("exact"), 201, "cash exact")
    check(exact["change_due"] == "0.00", "exact change zero")
    higher_body = payload("higher", cash_received=str(price + Decimal("10.00")))
    higher = call(vendedor, "/api/sales", higher_body, 201, "cash higher")
    check(higher["change_due"] == "10.00", "higher change ten")
    before = business()
    call(vendedor, "/api/sales", payload("insufficient", cash_received="0.01"), 400, "insufficient cash")
    missing = payload("missing"); missing.pop("cash_received")
    call(vendedor, "/api/sales", missing, 400, "missing cash")
    for role in ("admin", "inventario", "soporte_ti"):
        call(clients[role], "/api/sales", payload("forbidden-" + role), 403, role + " cannot sell")
    check(business() == before, "rejected sales preserve stock and rows")
    replay = call(vendedor, "/api/sales", higher_body, 200, "idempotent sale")
    check(replay == higher and business() == before, "sale replay no second exit")
    transfer = payload("transfer", payment_method="transfer"); transfer.pop("cash_received")
    noncash = call(vendedor, "/api/sales", transfer, 201, "noncash without cash_received")
    check(noncash["cash_received"] is None and noncash["change_due"] is None, "noncash NULL amounts")
    detail = clients["admin"].get(BASE + "/api/sales/" + exact["sale_number"], timeout=30)
    check(detail.status_code == 200, "admin can review sale")
    REPORT["sales"] = [{key: sale[key] for key in ("id", "total", "cash_received", "change_due")} for sale in (exact, higher, noncash)]
    save()


def purchase_cases(clients):
    with TARGET.connect() as c:
        supplier = c.execute(text("SELECT id FROM suppliers WHERE is_active=1 ORDER BY id LIMIT 1")).scalar_one()
        legacy = c.execute(text("SELECT id FROM purchase_orders WHERE status='pending' ORDER BY id LIMIT 1")).scalar()
    body = {"supplier_id": supplier, "notes": "Cierre DEMO 5001 20261004",
            "items": [{"product_id": 14, "quantity": 4, "unit_cost": "1.95"}]}
    inv, admin = clients["inventario"], clients["admin"]
    order = call(inv, "/api/purchases", body, 201, "inventory creates purchase")
    check(order["status"] == "pending_approval", "purchase pending_approval")
    path = f"/api/purchases/{order['id']}"
    receipt = {"received_at": datetime.now(ZoneInfo("America/Caracas")).date().isoformat(),
               "items": [{"line_id": order["items"][0]["id"], "quantity": 4}]}
    before = business()
    call(inv, path + "/approve", {}, 403, "inventory cannot approve purchase")
    call(inv, path + "/receptions", receipt, 409, "unapproved purchase cannot receive")
    call(admin, "/api/purchases", body, 403, "admin cannot create purchase")
    call(admin, path + "/receptions", receipt, 403, "admin cannot receive purchase")
    if legacy:
        detail = inv.get(BASE + f"/api/purchases/{legacy}", timeout=30).json()
        legacy_body = {**receipt, "items": [{"line_id": detail["items"][0]["id"], "quantity": 1}]}
        call(inv, f"/api/purchases/{legacy}/receptions", legacy_body, 409, "legacy pending cannot receive")
    check(business() == before, "denied purchases preserve rows and stock")
    stocks = snapshot()["stock"]
    call(admin, path + "/approve", {}, 200, "admin approves purchase")
    check(snapshot()["stock"] == stocks, "purchase approval no stock change")
    call(inv, path + "/receptions", receipt, 200, "inventory receives approved purchase")
    before = business()
    call(inv, path + "/receptions", receipt, 409, "repeat receipt refused")
    check(business() == before, "receipt applied once")
    rejected = call(inv, "/api/purchases", body, 201, "second purchase request")
    stock = snapshot()["stock"]
    result = call(admin, f"/api/purchases/{rejected['id']}/reject", {"reason": "Caso sintético rechazado"}, 200, "admin rejects purchase")
    check(result["status"] == "rejected" and snapshot()["stock"] == stock, "purchase rejection no stock")
    REPORT["purchases"] = {"received": order["id"], "rejected": rejected["id"], "legacy_pending_checked": legacy}
    save()


def request_adjustment(clients, product, new_stock, label):
    return call(clients["inventario"], "/api/inventory/adjustment",
                {"product_id": product, "new_stock": new_stock, "reason": "Cierre DEMO 5001: " + label}, 201, label)


def adjustment_cases(clients):
    inv, admin = clients["inventario"], clients["admin"]
    stocks = snapshot()["stock"]
    row = request_adjustment(clients, 15, 46, "solicitud para aprobación concurrente")
    check(row["status"] == "pending" and row["stock_movement_id"] is None and snapshot()["stock"] == stocks, "pending preserves stock without movement")
    path = f"/api/inventory/adjustments/{row['id']}"
    before = business()
    call(inv, path + "/approve", {}, 403, "requester cannot self approve")
    for role in ("vendedor", "soporte_ti"):
        call(clients[role], "/api/inventory/adjustment", {"product_id": 15, "new_stock": 47, "reason": "Denied"}, 403, role + " cannot request adjustment")
        call(clients[role], path + "/approve", {}, 403, role + " cannot approve adjustment")
    check(business() == before, "denied adjustments preserve state")
    barrier = Barrier(2)
    def approve(_):
        client = requests.Session(); client.trust_env = False; client.cookies.update(admin.cookies)
        barrier.wait(timeout=10)
        return client.post(BASE + path + "/approve", json={"review_comment": "Revisión concurrente sintética"}, timeout=30)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(approve, (1, 2)))
    check(sorted(response.status_code for response in responses) == [200, 409], "InnoDB double approval serialized: 200/409")
    approved = next(response.json() for response in responses if response.status_code == 200)
    check(approved["status"] == "approved" and approved["reviewer_user_id"] == 1 and approved["stock_movement_id"] is not None, "approved links admin and movement")
    before = business()
    call(admin, path + "/approve", {}, 409, "second approval refused")
    call(admin, path + "/reject", {}, 409, "approved cannot reject")
    check(business() == before, "no second adjustment application")
    rejected = request_adjustment(clients, 15, 48, "solicitud para rechazo")
    stocks = snapshot()["stock"]
    rejected = call(admin, f"/api/inventory/adjustments/{rejected['id']}/reject", {"review_comment": "No procede"}, 200, "reject pending")
    before = business()
    call(admin, f"/api/inventory/adjustments/{rejected['id']}/approve", {}, 409, "rejected cannot approve")
    check(rejected["stock_movement_id"] is None and stocks == snapshot()["stock"] and business() == before, "rejection preserves stock no movement")
    pending = request_adjustment(clients, 16, 55, "pendiente para demostración")
    conflict = request_adjustment(clients, 14, 37, "conflicto por movimiento posterior")
    for action in ("entry", "exit"):
        call(inv, "/api/inventory/" + action, {"product_id": 14, "quantity": 1, "reason": "Cierre DEMO: cambio posterior"}, 201, "intervening " + action)
    before = business()
    call(admin, f"/api/inventory/adjustments/{conflict['id']}/approve", {}, 409, "stale request refuses round trip stock")
    check(business() == before, "conflict no silent overwrite")
    call(admin, f"/api/inventory/adjustments/{conflict['id']}/reject", {"review_comment": "Stock cambió; solicitud obsoleta"}, 200, "reject stale request")
    REPORT["adjustments"] = {"approved": approved, "rejected": rejected, "pending": pending, "conflict": conflict["id"]}
    save()


def invariants(baseline):
    after = snapshot()
    expected = {"sales": 3, "sale_lines": 3, "stock_movements": 7, "purchase_orders": 2,
                "purchase_receptions": 1, "purchase_reception_lines": 1, "inventory_adjustment_requests": 4}
    for table, count in after["counts"].items():
        check(count - baseline["counts"].get(table, 0) == expected.get(table, 0), "exact delta " + table)
    before_stock = {row["id"]: row["current_stock"] for row in baseline["stock"]}
    for row in after["stock"]:
        assert row["current_stock"] == before_stock[row["id"]] + {14: 1, 15: 2}.get(row["id"], 0)
    check(True, "all 50 stocks match expected deltas")
    with TARGET.connect() as c:
        movements = rows(c, "SELECT product_id,previous_stock,new_stock FROM stock_movements ORDER BY product_id,id")
        current = {}
        for movement in movements:
            pid = movement["product_id"]
            assert movement["previous_stock"] == current.get(pid, movement["previous_stock"])
            current[pid] = movement["new_stock"]
        assert all(current.get(row["id"], row["current_stock"]) == row["current_stock"] for row in after["stock"])
        check(True, "movement chain matches current stock for all products")
        check(c.execute(text("SELECT COUNT(*) FROM sales WHERE id<=:max AND (cash_received IS NOT NULL OR change_due IS NOT NULL)"), {"max": baseline["sales_max"]}).scalar_one() == 0, "all historical sales retain NULL cash")
        for table, field in (("sales", "idempotency_key"), ("purchase_reception_lines", "stock_movement_id"), ("inventory_adjustment_requests", "stock_movement_id")):
            duplicates = c.execute(text(f"SELECT COUNT(*) FROM (SELECT {field} FROM {table} WHERE {field} IS NOT NULL GROUP BY {field} HAVING COUNT(*)>1) duplicate_rows")).scalar_one()
            check(duplicates == 0, "zero duplicates " + table)
    check(after["seed"] == baseline["seed"], "seed marker unchanged")
    return after


def run():
    global REPORT
    if LEDGER.exists():
        raise RuntimeError("Existing task ledger: use check; never replay mutations blindly.")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    REPORT = {"checks": [], "baseline": baseline, "phase": "running"}; save()
    start = snapshot()
    check(start["revision"] == "0004_inventory_adjustments", "Alembic HEAD before transactions")
    check(start["stock"] == baseline["stock"] and start["users"] == baseline["users"], "migration preserves stocks and four roles")
    from app import create_app
    from app.utils.permissions import role_has_permission
    app = create_app()
    with app.app_context():
        codes = ("sales:create", "sales:read", "purchases:write", "purchases:approve", "purchases:receive", "inventory_adjustments:create", "inventory_adjustments:approve")
        effective = {role: {code: role_has_permission(role, code) for code in codes} for role in ("admin", "inventario", "vendedor", "soporte_ti")}
        for code, allowed in {"sales:create": "vendedor", "purchases:write": "inventario", "purchases:receive": "inventario", "purchases:approve": "admin", "inventory_adjustments:create": "inventario", "inventory_adjustments:approve": "admin"}.items():
            check(all(effective[role][code] == (role == allowed) for role in effective), "effective DB permission " + code)
        REPORT["effective_permissions"] = effective
    actors = clients(app)
    sale_cases(actors); purchase_cases(actors); adjustment_cases(actors)
    REPORT["final"] = invariants(baseline)
    REPORT["phase"] = "complete"; save()
    print("COMPLETE: DEMO transactions and invariants", flush=True)


def main():
    global TARGET, REPORT
    parser = argparse.ArgumentParser(); parser.add_argument("mode", choices=("baseline", "run", "check"))
    args = parser.parse_args()
    assert validate_demo() == "conejo_demo_8b867d4b"
    TARGET = engine()
    with TARGET.connect() as connection:
        identity(connection); require_ready(connection)
    if args.mode == "baseline":
        if BASELINE.exists(): raise RuntimeError("Baseline already exists")
        state = snapshot(); BASELINE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        print(json.dumps({key:state[key] for key in ("revision", "counts", "users")}, indent=2))
    elif args.mode == "run":
        run()
    else:
        REPORT = json.loads(LEDGER.read_text(encoding="utf-8"))
        assert REPORT["phase"] == "complete"
        check(snapshot() == REPORT["final"], "exact persisted schema data permissions states reviewer movement")
        client = requests.Session(); client.trust_env = False
        check(client.get(BASE + "/login", timeout=30).status_code == 200, "DEMO 5001 login HTTP 200")


if __name__ == "__main__":
    main()
