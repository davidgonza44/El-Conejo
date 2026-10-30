"""HTTP/MariaDB exclusivamente DEMO 5001; fixtures propios y limpieza por sus IDs.

--reproduce ejecuta el código actual sin exigir las correcciones de UI.
--visual deja una OC sintética pendiente de cierre para decidirla en el navegador.
No lee dotenv, credenciales, archivos privados, informes ni volúmenes.
"""
import json
import re
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docker_support.guard import validate_demo, validate_demo_url
from verify_demo_category_approval import authenticated, expect

TABLES = ("products", "suppliers", "purchase_orders", "purchase_order_lines",
          "purchase_receptions", "purchase_reception_lines", "stock_movements",
          "supplier_quotes", "supplier_quote_lines", "replenishment_requests",
          "replenishment_request_lines", "purchase_closure_requests", "product_state_histories")


def checksums(engine):
    from sqlalchemy import text
    with engine.connect() as connection:
        rows = connection.execute(text("CHECKSUM TABLE " + ", ".join(TABLES))).all()
        assert all(row[1] is not None for row in rows), "Checksums no disponibles"
        return rows


class DemoAudit:
    def __init__(self, app):
        self.app = app
        self.prefix = "AUDIT-VAC-" + uuid.uuid4().hex[:10]
        self.users, self.products, self.suppliers = {}, [], []
        self.today = datetime.now().date().isoformat()

    def seed(self):
        from app.extensions import db
        from app.models import Category, Product, User
        from app.models.purchase import Supplier
        from app.services.inventory_service import COMMERCIAL_ZONE
        self.today = datetime.now(COMMERCIAL_ZONE).date().isoformat()
        with self.app.app_context():
            category_id = Category.query.filter_by(approval_status="approved", is_active=True).first().id
            for role in ("asistente", "inventario", "admin", "admin2", "vendedor"):
                user = User(name=self.prefix + " " + role, username=self.prefix + "-" + role,
                            email=self.prefix + "-" + role + "@example.invalid",
                            role="admin" if role == "admin2" else role)
                db.session.add(user)
                db.session.flush()
                self.users[role] = user.id
            for index in range(2):
                supplier = Supplier(name=self.prefix + " Proveedor " + str(index + 1),
                                    tax_id=self.prefix + "-SUP-" + str(index), approval_status="approved")
                product = Product(code=self.prefix + "-P" + str(index), name=self.prefix + " Producto " + str(index + 1),
                                  category_id=category_id, current_stock=17 + index * 6, minimum_stock=0,
                                  purchase_price="2.50", sale_price="4.00", is_active=True, approval_status="approved")
                db.session.add_all([supplier, product])
                db.session.flush()
                self.suppliers.append(supplier.id)
                self.products.append(product.id)
            db.session.commit()

    def order(self, clients):
        items = [{"product_id": product_id, "quantity": 3 + index * 2, "unit_price": "7.25" if index == 0 else "11.40"}
                 for index, product_id in enumerate(self.products)]
        quote = expect(clients["asistente"], "POST", "/api/supplier-quotes", 201, json={
            "supplier_id": self.suppliers[0], "issued_on": self.today, "valid_until": "2099-01-01",
            "currency": "VES", "notes": self.prefix, "items": items})
        row = expect(clients["asistente"], "POST", "/api/purchases", 201, json={
            "supplier_id": self.suppliers[0], "supplier_quote_id": quote["id"], "notes": self.prefix,
            "items": [{"product_id": item["product_id"], "quantity": item["quantity"]} for item in items]})
        return expect(clients["admin"], "POST", f"/api/purchases/{row['id']}/approve", 200, json={})

    def request(self, clients, order):
        return expect(clients["asistente"], "POST", f"/api/purchases/{order['id']}/closure-requests", 200,
                      json={"kind": "unavailable", "reason": self.prefix + ": proveedor confirma que no despachará mercancía."})

    def evidence(self, order_id):
        from app.extensions import db
        from app.models import Product, StockMovement
        from app.models.purchase import PurchaseOrder, PurchaseReception
        from app.models.purchase_closure import PurchaseClosureRequest
        with self.app.app_context():
            order = db.session.get(PurchaseOrder, order_id)
            trace = PurchaseClosureRequest.query.filter_by(purchase_order_id=order_id).order_by(PurchaseClosureRequest.id.desc()).first()
            return {"id": order.id, "number": order.order_number, "status": order.status,
                    "supplier_id": order.supplier_id, "supplier": order.supplier.name,
                    "supplier_quote_id": order.supplier_quote_id,
                    "replenishment_request_id": order.replenishment_request_id,
                    "subtotal": str(order.subtotal), "total": str(order.total),
                    "closed_at": order.closed_at.isoformat() if order.closed_at else None,
                    "closed_by_user_id": order.closed_by_user_id, "closure_reason": order.closure_reason,
                    "products": [{"id": line.product_id, "code": line.product_code, "name": line.product_name,
                                  "ordered": line.quantity_ordered, "received": line.quantity_received,
                                  "cost": str(line.unit_cost), "total": str(line.line_total)} for line in order.lines],
                    "stock": {str(key): db.session.get(Product, key).current_stock for key in self.products},
                    "reference_cost": {str(key): str(db.session.get(Product, key).purchase_price) for key in self.products},
                    "receptions": PurchaseReception.query.filter_by(purchase_order_id=order_id).count(),
                    "movements": StockMovement.query.filter(StockMovement.product_id.in_(self.products)).count(),
                    "trace": trace.to_dict() if trace else None}

    def preserved(self, before, after):
        for key in ("supplier_id", "supplier", "supplier_quote_id", "replenishment_request_id", "subtotal", "total", "products", "stock", "reference_cost", "receptions", "movements"):
            assert after[key] == before[key], (key, before[key], after[key])

    def race(self, clients, order, operations):
        barrier = Barrier(2)
        path = f"/api/purchases/{order['id']}"
        payload = {"received_at": self.today, "items": [{"line_id": order["items"][0]["id"], "quantity": 1}]}
        def send(operation):
            actor, tail, data = operation
            barrier.wait(timeout=15)
            response = clients[actor].post("http://host.docker.internal:5001" + path + tail, json=data, timeout=30)
            return response.status_code
        choices = {
            "approve": ("admin", "/closure-requests/approve", {}),
            "approve2": ("admin2", "/closure-requests/approve", {}),
            "reject2": ("admin2", "/closure-requests/reject", {"reason": "Carrera de prueba"}),
            "receive": ("inventario", "/receptions", payload),
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(send, [choices[key] for key in operations]))

    def stale_request_race(self, order):
        """Establece dos snapshots antes del lock: reproduce REPEATABLE READ."""
        from flask_login import login_user
        from app.extensions import db
        from app.models import User
        from app.services import purchase_service
        from app.services.exceptions import ConflictError
        locked = purchase_service.get_order
        barrier = Barrier(2)
        def snapshot_then_lock(key, lock=False):
            if lock:
                locked(key)
                barrier.wait(timeout=15)
            return locked(key, lock=lock)
        def send(index):
            with self.app.test_request_context():
                login_user(db.session.get(User, self.users["asistente"]))
                try:
                    purchase_service.request_closure(order["id"], {"kind": "unavailable", "reason": "Snapshot " + str(index)})
                    return 200
                except ConflictError:
                    return 409
        with patch.object(purchase_service, "get_order", side_effect=snapshot_then_lock):
            with ThreadPoolExecutor(max_workers=2) as pool:
                return list(pool.map(send, range(2)))

    def verify(self, clients, reproduce=False):
        if not reproduce:
            self.ui_roles(clients)
        order = self.order(clients)
        before = self.evidence(order["id"])
        self.request(clients, order)
        for actor in ("asistente", "inventario", "vendedor"):
            expect(clients[actor], "POST", f"/api/purchases/{order['id']}/closure-requests/approve", 403, json={})
        page = expect(clients["admin"], "GET", "/api/purchases?closure_status=pending&start=&end=", 200)
        unrelated = [row["id"] for row in page["items"] if not row.get("closure_request") or row["closure_request"]["status"] != "pending"]
        print("FILTER " + ("FAIL" if unrelated else "PASS") + ": unrelated=" + str(unrelated), flush=True)
        if not reproduce:
            assert not unrelated
        expect(clients["admin"], "POST", f"/api/purchases/{order['id']}/closure-requests/approve", 200, json={})
        after = self.evidence(order["id"])
        assert after["status"] == "closed_unavailable"
        self.preserved(before, after)
        print("EVIDENCE " + json.dumps({"before": before, "after": after}, ensure_ascii=False), flush=True)
        for tail in ("approve", "reject"):
            expect(clients["admin"], "POST", f"/api/purchases/{order['id']}/closure-requests/{tail}", 409,
                   json={} if tail == "approve" else {"reason": "Reintento"})
        order = self.order(clients)
        race = self.stale_request_race(order)
        print("STALE REQUEST RACE " + str(race), flush=True)
        if not reproduce:
            assert sorted(race) == [200, 409], race
            self.http_contract(clients)
        for decisions in (("approve", "approve2"), ("approve", "reject2"), ("approve", "receive")):
            for attempt in range(3):
                order = self.order(clients)
                self.request(clients, order)
                before_race = self.evidence(order["id"])
                results = self.race(clients, order, decisions)
                assert sorted(results) == [200, 409], (decisions, results)
                state = self.evidence(order["id"])
                assert not (state["status"] == "closed_unavailable" and state["receptions"] > 0)
                self.validate_race_state(before_race, state, reproduce)
                print("HTTP RACE " + str(decisions) + " " + str(results) + " -> " + state["status"], flush=True)
        print("PASS HTTP DEMO: cierre intacto, RBAC, decisiones repetidas y carreras reales.", flush=True)

    def validate_race_state(self, before, after, reproduce):
        if after["status"] == "closed_unavailable":
            self.preserved(before, after)
        elif not reproduce and after["status"] == "closed_partial":
            assert after["trace"]["status"] == "cancelled"
            assert after["receptions"] == 1 and after["movements"] == before["movements"] + 1
            product = str(self.products[0])
            assert after["stock"][product] == before["stock"][product] + 1

    def ui_roles(self, clients):
        for actor in ("asistente", "admin", "inventario"):
            html = expect(clients[actor], "GET", "/purchases?start=&end=", 200)
            match = re.search(r'<script type="application/json" id="po-context">(.*?)</script>', html, re.S)
            assert match, actor
            context = json.loads(match[1])
            assert context["canCloseRequest"] == (actor == "asistente")
            assert context["canCloseApprove"] == (actor == "admin")
            if actor == "admin":
                assert "Cierres pendientes" in html
        print("PASS HTML real: asistente solicita, admin decide, inventario no solicita/decide.", flush=True)

    def http_contract(self, clients):
        from app.extensions import db
        from app.models.purchase import PurchaseOrder
        order = self.order(clients)
        path = f"/api/purchases/{order['id']}"
        before = self.evidence(order["id"])
        for reason in (None, "", " \n\t ", 23, [], "x" * 1001):
            expect(clients["asistente"], "POST", path + "/closure-requests", 400,
                   json={"kind": "unavailable", "reason": reason})
        self.request(clients, order)
        expect(clients["asistente"], "POST", path + "/closure-requests", 409,
               json={"kind": "unavailable", "reason": "Duplicada"})
        for changes in ({"supplier_id": self.suppliers[1]}, {"items": []}, {"quantity": 99}, {"unit_cost": "99.00"}):
            expect(clients["admin"], "POST", path + "/closure-requests/approve", 400, json=changes)
        expect(clients["admin"], "POST", path + "/closure-requests/reject", 400, json={"reason": " "})
        expect(clients["admin"], "POST", path + "/closure-requests/reject", 200, json={"reason": "Proveedor confirma nueva disponibilidad"})
        after = self.evidence(order["id"])
        assert after["status"] == "approved" and after["trace"]["status"] == "rejected"
        self.preserved(before, after)
        expect(clients["admin"], "POST", path + "/closure-requests/approve", 409, json={})
        received = expect(clients["inventario"], "POST", path + "/receptions", 200, json={
            "received_at": self.today, "items": [{"line_id": line["id"], "quantity": line["quantity_ordered"]} for line in order["items"]]})
        assert received["status"] == "closed_complete"
        for status in ("pending_approval", "rejected", "closed_complete", "closed_partial", "closed_price_change", "cancelled"):
            order = self.order(clients)
            with self.app.app_context():
                db.session.get(PurchaseOrder, order["id"]).status = status
                db.session.commit()
            expect(clients["asistente"], "POST", f"/api/purchases/{order['id']}/closure-requests", 409,
                   json={"kind": "unavailable", "reason": "Estado inválido"})
        expect(clients["asistente"], "POST", "/api/purchases/2147483647/closure-requests", 404,
               json={"kind": "unavailable", "reason": "Inexistente"})
        expect(clients["admin"], "POST", "/api/purchases/2147483647/closure-requests/approve", 404, json={})
        order = self.order(clients)
        path = f"/api/purchases/{order['id']}"
        self.request(clients, order)
        expect(clients["admin"], "POST", path + "/closure-requests/approve", 200, json={})
        before = self.evidence(order["id"])
        expect(clients["asistente"], "POST", path + "/duplicate", 409, json={})
        for changes in ({"supplier_id": self.suppliers[1]}, {"items": []}, {"notes": "Cambio"},
                        {"items": [{"product_id": self.products[0], "quantity": 99, "unit_cost": "99.00"}]}):
            expect(clients["asistente"], "PATCH", path, 409, json=changes)
        expect(clients["asistente"], "POST", path + "/closure-requests", 409,
               json={"kind": "unavailable", "reason": "Repetida"})
        expect(clients["inventario"], "POST", path + "/receptions", 409, json={
            "received_at": self.today, "items": [{"line_id": order["items"][0]["id"], "quantity": 1}]})
        self.new_need(clients, order)
        self.preserved(before, self.evidence(order["id"]))
        # Force reception to win as well as the simultaneous HTTP races.
        partial = self.order(clients)
        self.request(clients, partial)
        expect(clients["inventario"], "POST", f"/api/purchases/{partial['id']}/receptions", 200, json={
            "received_at": self.today, "items": [{"line_id": partial["items"][0]["id"], "quantity": 1}]})
        assert self.evidence(partial["id"])["trace"]["status"] == "cancelled"
        expect(clients["admin"], "POST", f"/api/purchases/{partial['id']}/closure-requests/approve", 409, json={})
        print("PASS HTTP: 6 estados inválidos, motivo 400, 404, rechazo/recepción, inmutabilidad, nueva SR/OC y recepción ganadora.", flush=True)

    def new_need(self, clients, original):
        request = expect(clients["inventario"], "POST", "/api/replenishment-requests", 201,
                         json={"items": [{"product_id": key, "quantity": 2} for key in self.products]})
        path = f"/api/replenishment-requests/{request['id']}"
        expect(clients["asistente"], "POST", path + "/start", 200, json={})
        quote = expect(clients["asistente"], "POST", path + "/quotes", 201, json={
            "supplier_id": self.suppliers[1], "currency": "VES", "issued_on": self.today,
            "valid_until": "2099-01-01", "items": [{"product_id": key, "unit_price": "9.50"} for key in self.products]})
        prepared = expect(clients["asistente"], "POST", path + "/prepare", 200, json={"supplier_quote_id": quote["id"]})
        new = expect(clients["asistente"], "GET", f"/api/purchases/{prepared['purchase_order_id']}", 200)
        assert new["order_number"] != original["order_number"] and new["status"] == "pending_approval"
        assert new["supplier"]["id"] == self.suppliers[1] and all(line["unit_cost"] == "9.50" for line in new["items"])
        expect(clients["admin"], "POST", f"/api/purchases/{new['id']}/approve", 200, json={})
        print("PASS new need: " + request["request_number"] + " -> " + quote["quote_number"] + " -> " + new["order_number"] + " nueva aprobación.", flush=True)

    def visual(self, clients, *, reject=False):
        self.ui_roles(clients)
        order = self.order(clients)
        before = self.evidence(order["id"])
        self.request(clients, order)
        print("VISUAL " + json.dumps(before, ensure_ascii=False), flush=True)
        input("OC lista: aprobar/rechazar en UI, luego Enter para verificar y limpiar.\n")
        after = self.evidence(order["id"])
        assert after["status"] == ("approved" if reject else "closed_unavailable"), after["status"]
        assert after["trace"]["status"] == ("rejected" if reject else "approved")
        if not reject:
            assert after["closed_by_user_id"] == after["trace"]["decided_by_user_id"]
            assert after["closed_at"] == after["trace"]["decided_at"]
            assert after["closure_reason"] == after["trace"]["reason"]
        self.preserved(before, after)
        print("VISUAL FINAL " + json.dumps(after, ensure_ascii=False), flush=True)
        if reject:
            received = expect(clients["inventario"], "POST", f"/api/purchases/{order['id']}/receptions", 200, json={
                "received_at": self.today, "items": [{"line_id": line["id"], "quantity": line["quantity_ordered"]} for line in order["items"]]})
            assert received["status"] == "closed_complete"
            print("PASS visual rechazo: OC approved, condiciones/stock intactos, recepción normal posterior closed_complete.", flush=True)

    def cleanup(self):
        from app.extensions import db
        from app.models import Product, User, StockMovement, ProductStateHistory, UserSession
        from app.models.purchase import Supplier, PurchaseOrder, PurchaseOrderLine, PurchaseReception, PurchaseReceptionLine
        from app.models.purchase_closure import PurchaseClosureRequest
        from app.models.supplier_quote import SupplierQuote, SupplierQuoteLine
        from app.models.replenishment_request import ReplenishmentRequest, ReplenishmentRequestLine
        from app.models.notification import Notification, NotificationDelivery
        with self.app.app_context():
            db.session.rollback()
            ids = list(self.users.values())
            assert all(row.username.startswith(self.prefix) for row in User.query.filter(User.id.in_(ids)))
            assert all(row.code.startswith(self.prefix) for row in Product.query.filter(Product.id.in_(self.products)))
            assert all(row.tax_id.startswith(self.prefix) for row in Supplier.query.filter(Supplier.id.in_(self.suppliers)))
            orders = [row.id for row in PurchaseOrder.query.filter(PurchaseOrder.created_by_user_id.in_(ids))]
            receipts = [row.id for row in PurchaseReception.query.filter(PurchaseReception.purchase_order_id.in_(orders))]
            requests = [row.id for row in ReplenishmentRequest.query.filter(ReplenishmentRequest.created_by_user_id.in_(ids))]
            quotes = [row.id for row in SupplierQuote.query.filter(SupplierQuote.created_by_user_id.in_(ids))]
            notifications = [row.id for row in Notification.query.filter(
                (Notification.user_id.in_(ids)) | ((Notification.entity_type == "purchase_order") & Notification.entity_id.in_(orders)))]
            for model, condition in (
                (NotificationDelivery, NotificationDelivery.notification_id.in_(notifications)),
                (Notification, Notification.id.in_(notifications)),
                (PurchaseReceptionLine, PurchaseReceptionLine.reception_id.in_(receipts)),
                (PurchaseReception, PurchaseReception.id.in_(receipts)),
                (PurchaseClosureRequest, PurchaseClosureRequest.purchase_order_id.in_(orders)),
                (ReplenishmentRequestLine, ReplenishmentRequestLine.request_id.in_(requests)),
                (ReplenishmentRequest, ReplenishmentRequest.id.in_(requests)),
                (PurchaseOrderLine, PurchaseOrderLine.purchase_order_id.in_(orders)),
                (PurchaseOrder, PurchaseOrder.id.in_(orders)),
                (SupplierQuoteLine, SupplierQuoteLine.quote_id.in_(quotes)),
                (SupplierQuote, SupplierQuote.id.in_(quotes)),
                (StockMovement, StockMovement.product_id.in_(self.products)),
                (ProductStateHistory, ProductStateHistory.product_id.in_(self.products)),
                (Product, Product.id.in_(self.products)),
                (Supplier, Supplier.id.in_(self.suppliers)),
                (UserSession, UserSession.user_id.in_(ids)),
                (User, User.id.in_(ids)),
            ):
                model.query.filter(condition).delete(synchronize_session=False)
            db.session.commit()
        print("PASS cleanup: solo IDs propios eliminados.", flush=True)


def main():
    if sys.argv[1:] not in ([], ["--reproduce"], ["--visual"], ["--visual-reject"]):
        raise SystemExit("Uso: verify_demo_purchase_unavailable.py [--reproduce|--visual|--visual-reject]")
    validate_demo()
    from docker_support.bootstrap import engine
    from docker_support.demo import require_ready
    from app import create_app
    target = engine()
    validate_demo_url(target.url)
    with target.connect() as connection:
        require_ready(connection)
    before = checksums(target)
    app = create_app()
    from sqlalchemy.engine import make_url
    validate_demo_url(make_url(app.config["SQLALCHEMY_DATABASE_URI"]))
    assert app.config["MAIL_ENABLED"] is False and app.config["NOTIFICATIONS_DRY_RUN"] is True
    audit = DemoAudit(app)
    try:
        audit.seed()
        with ExitStack() as stack:
            clients = {role: stack.enter_context(authenticated(app, key)) for role, key in audit.users.items()}
            if sys.argv[1:] in (["--visual"], ["--visual-reject"]):
                audit.visual(clients, reject=sys.argv[1:] == ["--visual-reject"])
            else:
                audit.verify(clients, reproduce=sys.argv[1:] == ["--reproduce"])
    finally:
        audit.cleanup()
        assert checksums(target) == before, "Datos originales cambiaron"
        print("PASS checksums: 13 tablas originales intactas; sin operaciones sobre volúmenes.", flush=True)


if __name__ == "__main__":
    main()
