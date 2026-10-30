"""Isolated inventory core tests; temporary SQLite, never the operational database."""
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from test_products_core import setup_app, create_via_api
from app.extensions import db
from app.models import StockMovement, Product
from app.services import inventory_service as service
from app.utils.permissions import ROLE_PERMISSIONS, INVENTORY_READ


def run():
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1

    with tempfile.TemporaryDirectory(prefix="inventory-core-") as folder:
        app, admin, inv, seller, ids = setup_app(Path(folder))
        app.logger.disabled = True  # Expected injected failures are asserted below.
        response = create_via_api(admin, code="INV_CORE", name="Martillo prueba", category_id=ids["construccion"], current_stock=10)
        product = response.json["id"]
        endpoint = "/api/inventory"
        first = admin.get(endpoint + "/movements?page=1&per_page=8").json["items"][0]

        def post(kind, **data):
            return admin.post(endpoint + "/" + kind, json={"product_id": product, **data})

        for kind, body, expected in [
            ("entry", {"quantity": 4, "reason": "Recepción"}, 14),
            ("exit", {"quantity": 3}, 11),
            ("adjustment", {"new_stock": 20, "reason": "Conteo arriba"}, 20),
            ("adjustment", {"new_stock": 8, "reason": "Conteo abajo"}, 8),
        ]:
            response = post(kind, **body)
            check(response.status_code == 201, kind)
            check(response.json["product"]["current_stock"] == expected, "stock " + kind)
            movement = response.json["movement"]
            check(movement["user_id"] == ids["admin_id"], "authenticated user")
            check(movement["quantity"] == abs(movement["new_stock"] - movement["previous_stock"]), "quantity magnitude")
        check(post("exit", quantity=100).status_code == 409, "insufficient stock")
        for value in [True, False, 1.5, 2.0, "1.5", 0, -1, None, [], {}]:
            for kind in ("entry", "exit"):
                check(post(kind, quantity=value).status_code == 400, "invalid quantity")
        for value in [True, 1.5, "2.5", -1, None]:
            check(post("adjustment", new_stock=value, reason="Conteo").status_code == 400, "invalid target")
        for reason in [None, True, 23, [], "x" * 256]:
            check(post("entry", quantity=1, reason=reason).status_code == 400, "invalid reason")
        check(post("adjustment", new_stock=3, reason=" ").status_code == 400, "required reason")
        check(post("adjustment", new_stock=8, reason="Igual").status_code == 400, "no-op")
        for kind, body in [("entry", {"quantity": 1}), ("exit", {"quantity": 1}), ("adjustment", {"new_stock": 3, "reason": "x"})]:
            check(post(kind, **body, user_id=ids["admin_id"]).status_code == 400, "body user rejection")

        rows = admin.get(endpoint + "/movements").json
        check(set(rows) == {"count", "items"} and rows["count"] == 5, "legacy shape")
        check(admin.get(endpoint + "/movements?limit=2").json["count"] == 2, "legacy limit")
        check(admin.get(endpoint + f"/products/{product}/movements?limit=2").json["count"] == 2, "product history")
        for search in ["Martillo", "INV_CORE", "Conteo"]:
            data = admin.get(endpoint + "/movements", query_string={"page": 1, "search": search}).json
            check(data["total"] == (2 if search == "Conteo" else 5), "search " + search)
        check(admin.get(endpoint + "/movements?page=1&search=%25").json["total"] == 0, "literal wildcard")
        for kind, total in [("entrada", 2), ("salida", 1), ("ajuste", 2)]:
            check(admin.get(endpoint + "/movements?page=1&movement_type=" + kind).json["total"] == total, "type filter")
        check(admin.get(endpoint + f"/movements?page=1&user_id={ids['admin_id']}").json["total"] == 5, "user filter")
        check(admin.get(endpoint + "/movements?page=1&user_id=99999").json["total"] == 0, "empty user")
        page = admin.get(endpoint + "/movements?page=2&per_page=2&sort=created_at").json
        check(page["total"] == 5 and page["pages"] == 3 and page["count"] == 2, "pagination")
        ordered = admin.get(endpoint + "/movements?page=1&sort=quantity").json["items"]
        check([m["quantity"] for m in ordered] == sorted(m["quantity"] for m in ordered), "sorting")
        for query in ["page=0", "per_page=101", "page=x", "sort=reason;drop", "page=1&limit=2", "movement_type=transferencia", "branch_id=1", "status=Completado", "date_from=x", "date_from=2025-02-02&date_to=2025-02-01"]:
            check(admin.get(endpoint + "/movements?" + query).status_code == 400, "invalid filter " + query)
        check(admin.get(endpoint + "/movements/999999").status_code == 404, "detail missing")
        detail = admin.get(endpoint + f"/movements/{first['id']}").json
        check(detail["presentation"] == {"status_label": "Aplicado", "reference": first["movement_number"]}, "derived presentation")
        check(detail == first, "original immutable")
        for method in (admin.put, admin.patch, admin.delete):
            check(method(endpoint + f"/movements/{first['id']}", json={"quantity": 50}).status_code == 405, "no mutation endpoint")
        check(set(admin.get(endpoint + "/movements/users").json["items"][0]) == {"id", "name"}, "public users")
        with app.app_context():
            movements = StockMovement.query.order_by(StockMovement.id).all()
            for movement, timestamp in zip(movements, [datetime(2025, 1, 2, 3, 59, 59), datetime(2025, 1, 2, 4), datetime(2025, 1, 3, 3, 59, 59), datetime(2025, 1, 3, 4), datetime(2025, 1, 2, 12)]):
                movement.created_at = timestamp  # Synthetic fixtures only.
            db.session.commit()
            summary = service.movement_summary({}, now=datetime(2025, 1, 2, 15, tzinfo=timezone.utc))
            check(summary["daily"] == {"entrada": 1, "salida": 1, "ajuste": 1}, "Caracas daily boundaries/counts")
        filtered = admin.get(endpoint + "/movements?page=1&date_from=2025-01-02&date_to=2025-01-02").json
        check(filtered["total"] == 3, "inclusive commercial date range")
        summary = admin.get(endpoint + "/movements/summary?movement_type=ajuste").json
        check(summary["distribution"] == {"ajuste": 2} and summary["total"] == 2, "group counts")
        check(len(summary["recent"]) == 2 and summary["recent"][0]["new_stock"] == 20, "recent ordering")
        check(app.test_client().get(endpoint + "/movements").status_code == 401, "anonymous")
        check(seller.get(endpoint + "/movements").status_code == 403, "seller")
        check(inv.get(endpoint + "/movements").status_code == 200, "inventory read")
        with patch.dict(ROLE_PERMISSIONS, {"inventario": {INVENTORY_READ}}):
            check(inv.get(endpoint + "/movements").status_code == 200, "read-only read")
            check(inv.post(endpoint + "/entry", json={"product_id": product, "quantity": 1}).status_code == 403, "read-only write denied")
        for method in ("flush", "commit"):
            with patch.object(db.session, method, side_effect=RuntimeError("synthetic failure")):
                check(post("entry", quantity=5).status_code == 500, "injected rollback " + method)
            with app.app_context():
                check(db.session.get(Product, product).current_stock == 8 and StockMovement.query.count() == 5, "atomic rollback " + method)
        check(post("adjustment", new_stock=0, reason="Conteo cero").status_code == 201, "zero target")
        with app.app_context():
            db.session.get(Product, product).is_active = False
            db.session.commit()
        check(post("entry", quantity=1).status_code == 409, "inactive product")
        check(admin.post(endpoint + "/entry", json={"product_id": 99999, "quantity": 1}).status_code == 404, "missing product")
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
        print(f"PASS: {checks} isolated backend checks. SQLite does not prove row locking.")


if __name__ == "__main__":
    run()
