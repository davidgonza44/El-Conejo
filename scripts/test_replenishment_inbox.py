"""Bandeja de solicitudes de reposición. SQLite en memoria.

No abre MySQL, el puerto 5000 ni la base de la empresa.
"""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from approval_test_support import LinkedSupplierCase as IsolatedCase
from app.extensions import db
from app.models import Product, StockMovement, User
from app.models.purchase import PurchaseOrder
from app.models.replenishment_request import ReplenishmentRequest, ReplenishmentRequestLine

MONEY = {"unit_cost", "unit_price", "purchase_price", "sale_price", "subtotal", "total",
         "pending_amount", "amount", "line_total"}


class ReplenishmentInboxTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.today = datetime.now(ZoneInfo("America/Caracas")).date().isoformat()
        soporte = User(name="soporte", username="soporte", email="soporte@example.invalid", role="soporte_ti")
        self.extra = Product(
            code="TEST-02", name="Segundo sintético", category_id=self.product.category_id,
            current_stock=4, minimum_stock=0, purchase_price=2, sale_price=3,
            is_active=True, approval_status="approved")
        db.session.add_all([soporte, self.extra])
        db.session.commit()
        self.associate(self.extra, self.supplier)
        self.clients["soporte"] = self.client_for(soporte)
        self.extra_id = self.extra.id

    def counts(self):
        db.session.expire_all()
        return {
            "sr": ReplenishmentRequest.query.count(),
            "po": PurchaseOrder.query.count(),
            "stock": db.session.get(Product, self.product.id).current_stock,
            "extra_stock": db.session.get(Product, self.extra_id).current_stock,
            "moves": StockMovement.query.count(),
        }

    def assert_no_money(self, payload):
        if isinstance(payload, dict):
            self.assertTrue(MONEY.isdisjoint(payload), sorted(MONEY.intersection(payload)))
            for value in payload.values():
                self.assert_no_money(value)
        elif isinstance(payload, list):
            for value in payload:
                self.assert_no_money(value)

    def create_sr(self, items=None, notes="Falta en anaquel"):
        payload = {"notes": notes, "items": items or [{"product_id": self.product.id, "quantity": 4}]}
        result = self.clients["inventario"].post("/api/replenishment-requests", json=payload)
        self.assertEqual(result.status_code, 201, result.get_json())
        return result.get_json()

    def quote(self, product_ids, price="8.50"):
        result = self.clients["asistente"].post("/api/supplier-quotes", json={
            "supplier_id": self.supplier.id, "issued_on": self.today, "currency": "VES",
            "valid_until": "2099-01-01",
            "items": [{"product_id": product_id, "quantity": 4, "unit_price": price} for product_id in product_ids],
        })
        self.assertEqual(result.status_code, 201, result.get_json())
        return result.get_json()

    def test_inventory_creates_and_assistant_finds_sr_without_mixing_purchases(self):
        before = self.counts()
        created = self.create_sr()
        self.assertEqual(created["origin"], "manual")
        self.assertEqual(created["status"], "open")
        self.assertIsNone(created["purchase_order"])
        self.assertEqual(created["items"][0]["stock_on_hand"], 10)
        self.assertEqual(created["items"][0]["quantity"], 4)
        self.assert_no_money(created)
        after = self.counts()
        self.assertEqual(after["sr"], before["sr"] + 1)
        self.assertEqual(after["po"], before["po"])
        self.assertEqual(after["stock"], before["stock"])
        self.assertEqual(after["moves"], before["moves"])

        db.session.expire_all()
        row = db.session.get(ReplenishmentRequest, created["id"])
        row.request_number = "SR-000003"
        db.session.commit()

        found = self.clients["asistente"].get("/api/replenishment-requests", query_string={"search": "SR-000003"})
        self.assertEqual(found.status_code, 200, found.get_json())
        body = found.get_json()
        self.assertEqual([item["request_number"] for item in body["items"]], ["SR-000003"])
        self.assert_no_money(body["items"])
        self.assertNotIn("unit_price", found.get_data(as_text=True))
        by_product = self.clients["asistente"].get(
            "/api/replenishment-requests", query_string={"search": "Producto sintético", "status": "open"})
        self.assertIn("SR-000003", [item["request_number"] for item in by_product.get_json()["items"]])
        by_date = self.clients["admin"].get(
            "/api/replenishment-requests", query_string={"search": "SR-000003", "start": self.today, "end": self.today})
        self.assertEqual(by_date.get_json()["items"][0]["request_number"], "SR-000003")
        self.assertEqual(self.clients["asistente"].get(
            "/api/replenishment-requests", query_string={"priority": "alta"}).status_code, 400)

        purchases = self.clients["asistente"].get("/api/purchases", query_string={"search": "SR-000003"})
        self.assertEqual(purchases.status_code, 200)
        self.assertEqual(purchases.get_json()["items"], [])
        self.assertNotIn("request_number", purchases.get_data(as_text=True))

        page = self.clients["asistente"].get("/purchases/requests", query_string={"search": "SR-000003"})
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("SR-000003", html)
        self.assertIn("Solicitudes de reposición", html)
        self.assertIn('href="/purchases/requests"', html)

        own = self.clients["inventario"].get("/api/replenishment-requests", query_string={"search": "SR-000003"})
        self.assertEqual(own.get_json()["items"][0]["request_number"], "SR-000003")
        self.assert_no_money(own.get_json()["items"])
        self.assertNotIn("unit_price", own.get_data(as_text=True))
        detail = self.clients["inventario"].get(f"/api/replenishment-requests/{created['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assert_no_money(detail.get_json())
        inv_page = self.clients["inventario"].get(f"/purchases/requests/{created['id']}").get_data(as_text=True)
        self.assertNotIn("unit_price", inv_page)
        self.assertNotIn("Iniciar procesamiento", inv_page)
        self.assertNotIn("$", inv_page)

        for client in (self.clients["vendedor"], self.clients["soporte"]):
            self.assertEqual(client.get("/purchases/requests").status_code, 403)
            self.assertEqual(client.get("/api/replenishment-requests").status_code, 403)
            self.assertEqual(client.get(f"/api/replenishment-requests/{created['id']}").status_code, 403)
        seller_nav = self.clients["vendedor"].get("/products").get_data(as_text=True)
        self.assertNotIn('href="/purchases/requests"', seller_nav)

    def test_other_inventory_request_stays_hidden_and_missing_is_404(self):
        hidden = ReplenishmentRequest(
            created_by_user_id=self.users["admin"].id, status="open", origin="manual", notes="Ajena")
        hidden.lines = [ReplenishmentRequestLine(
            product_id=self.product.id, product_code="TEST-01", product_name="Producto sintético",
            unit="unidad", quantity=1, stock_on_hand=10)]
        db.session.add(hidden)
        db.session.flush()
        hidden.request_number = "SR-999999"
        db.session.commit()
        hidden_id = hidden.id
        self.assertEqual(self.clients["inventario"].get(f"/api/replenishment-requests/{hidden_id}").status_code, 404)
        listed = self.clients["inventario"].get("/api/replenishment-requests", query_string={"search": "SR-999999"})
        self.assertEqual(listed.get_json()["items"], [])
        self.assertEqual(self.clients["asistente"].get(f"/api/replenishment-requests/{hidden_id}").status_code, 200)
        missing = self.clients["asistente"].get("/api/replenishment-requests/999999")
        self.assertEqual(missing.status_code, 404)
        self.assertIn("no encontrada", missing.get_json()["error"])

    def test_assistant_prepares_one_order_and_a_second_attempt_does_not_duplicate_it(self):
        created = self.create_sr()
        quote = self.quote([self.product.id])
        before = self.counts()
        early = self.clients["asistente"].post("/api/purchases", json={
            "supplier_id": self.supplier.id, "supplier_quote_id": quote["id"],
            "replenishment_request_id": created["id"],
            "items": [{"product_id": self.product.id, "quantity": 4}],
        })
        self.assertEqual(early.status_code, 409, early.get_json())
        self.assertEqual(self.counts()["po"], before["po"])
        blocked = self.clients["inventario"].post(f"/api/replenishment-requests/{created['id']}/start", json={})
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(self.clients["admin"].post(f"/api/replenishment-requests/{created['id']}/prepare", json={
            "supplier_quote_id": quote["id"]}).status_code, 403)

        started = self.clients["asistente"].post(f"/api/replenishment-requests/{created['id']}/start", json={})
        self.assertEqual(started.status_code, 200, started.get_json())
        self.assertEqual(started.get_json()["status"], "processing")
        again = self.clients["asistente"].post(f"/api/replenishment-requests/{created['id']}/start", json={})
        self.assertEqual(again.status_code, 409)
        self.assertEqual(self.counts()["po"], before["po"])
        self.assertEqual(self.counts()["stock"], before["stock"])

        short = self.clients["asistente"].post("/api/purchases", json={
            "supplier_id": self.supplier.id, "supplier_quote_id": quote["id"],
            "replenishment_request_id": created["id"],
            "items": [{"product_id": self.product.id, "quantity": 1}],
        })
        self.assertEqual(short.status_code, 400, short.get_json())
        self.assertIn("no se reparte", short.get_json()["error"])
        self.assertEqual(self.counts()["po"], before["po"])

        prepared = self.clients["asistente"].post(
            f"/api/replenishment-requests/{created['id']}/prepare", json={"supplier_quote_id": quote["id"]})
        self.assertEqual(prepared.status_code, 200, prepared.get_json())
        body = prepared.get_json()
        self.assertEqual(body["status"], "fulfilled")
        self.assertEqual(body["notes"], "Falta en anaquel")
        self.assertEqual(body["items"][0]["quantity"], 4)
        self.assertEqual(body["processed_by"]["id"], self.users["asistente"].id)
        self.assertIsNotNone(body["processed_at"])
        self.assert_no_money(body)
        order_id = body["purchase_order"]["id"]
        db.session.expire_all()
        order = db.session.get(PurchaseOrder, order_id)
        saved = db.session.get(ReplenishmentRequest, created["id"])
        self.assertEqual(order.replenishment_request_id, created["id"])
        self.assertEqual(saved.purchase_order_id, order.id)
        self.assertEqual(order.status, "pending_approval")
        self.assertEqual(self.counts()["po"], before["po"] + 1)
        self.assertEqual(self.counts()["stock"], before["stock"])
        self.assertEqual(self.counts()["moves"], before["moves"])

        duplicate = self.clients["asistente"].post(
            f"/api/replenishment-requests/{created['id']}/prepare", json={"supplier_quote_id": quote["id"]})
        self.assertEqual(duplicate.status_code, 409, duplicate.get_json())
        bypass = self.clients["asistente"].post("/api/purchases", json={
            "supplier_id": self.supplier.id, "supplier_quote_id": quote["id"],
            "replenishment_request_id": created["id"],
            "items": [{"product_id": self.product.id, "quantity": 4}],
        })
        self.assertEqual(bypass.status_code, 409, bypass.get_json())
        self.assertEqual(self.counts()["po"], before["po"] + 1)
        approved = self.clients["admin"].post(f"/api/purchases/{order_id}/approve", json={})
        self.assertEqual(approved.status_code, 200, approved.get_json())
        self.assertEqual(approved.get_json()["replenishment_request_id"], created["id"])
        self.assertEqual(self.counts()["stock"], before["stock"])
        db.session.expire_all()
        self.assertEqual(db.session.get(ReplenishmentRequest, created["id"]).status, "fulfilled")

    def test_several_products_stay_on_one_order_or_are_not_split(self):
        created = self.create_sr(items=[
            {"product_id": self.product.id, "quantity": 4},
            {"product_id": self.extra_id, "quantity": 2},
        ])
        partial = self.quote([self.product.id])
        self.clients["asistente"].post(f"/api/replenishment-requests/{created['id']}/start", json={})
        before = self.counts()
        rejected = self.clients["asistente"].post(
            f"/api/replenishment-requests/{created['id']}/prepare", json={"supplier_quote_id": partial["id"]})
        self.assertEqual(rejected.status_code, 400, rejected.get_json())
        self.assertIn("no se reparte", rejected.get_json()["error"])
        self.assertEqual(self.counts(), before)
        db.session.expire_all()
        self.assertEqual(db.session.get(ReplenishmentRequest, created["id"]).status, "processing")
        page = self.clients["asistente"].get(f"/purchases/requests/{created['id']}")
        self.assertIn("no se reparte", page.get_data(as_text=True))
        self.assertNotIn("Preparar orden de compra", page.get_data(as_text=True))

        quoted = self.clients["asistente"].post(f"/api/replenishment-requests/{created['id']}/quotes", json={
            "supplier_id": self.supplier.id, "currency": "VES", "issued_on": self.today, "valid_until": "2099-01-01",
            "items": [
                {"product_id": self.product.id, "unit_price": "3.25"},
                {"product_id": self.extra_id, "unit_price": "4.10"},
            ],
        })
        self.assertEqual(quoted.status_code, 201, quoted.get_json())
        self.assertIn("unit_price", quoted.get_json()["items"][0])
        self.assertEqual(self.counts()["po"], before["po"])
        self.assertEqual(self.clients["inventario"].get(f"/api/replenishment-requests/{created['id']}").get_json()["status"], "processing")
        self.assert_no_money(self.clients["inventario"].get(f"/api/replenishment-requests/{created['id']}").get_json())

        prepared = self.clients["asistente"].post(
            f"/api/replenishment-requests/{created['id']}/prepare", json={"supplier_quote_id": quoted.get_json()["id"]})
        self.assertEqual(prepared.status_code, 200, prepared.get_json())
        self.assertEqual(len(prepared.get_json()["items"]), 2)
        db.session.expire_all()
        order = db.session.get(PurchaseOrder, prepared.get_json()["purchase_order"]["id"])
        self.assertEqual(sorted(line.product_id for line in order.lines), sorted([self.product.id, self.extra_id]))
        self.assertEqual(sorted(line.quantity_ordered for line in order.lines), [2, 4])
        self.assertEqual(order.replenishment_request_id, created["id"])
        self.assertEqual(PurchaseOrder.query.filter_by(replenishment_request_id=created["id"]).count(), 1)
        self.assertEqual(self.counts()["extra_stock"], before["extra_stock"])

    def test_cancel_keeps_the_request_and_does_not_create_an_order(self):
        created = self.create_sr(notes="Se cancela")
        before = self.counts()
        cancelled = self.clients["asistente"].post(f"/api/replenishment-requests/{created['id']}/cancel", json={})
        self.assertEqual(cancelled.status_code, 200, cancelled.get_json())
        self.assertEqual(cancelled.get_json()["status"], "cancelled")
        self.assertEqual(cancelled.get_json()["notes"], "Se cancela")
        self.assertEqual(self.counts()["po"], before["po"])
        self.assertEqual(self.counts()["sr"], before["sr"])
        blocked = self.clients["asistente"].post(f"/api/replenishment-requests/{created['id']}/prepare", json={
            "supplier_quote_id": 1})
        self.assertEqual(blocked.status_code, 409)
        db.session.expire_all()
        saved = db.session.get(ReplenishmentRequest, created["id"])
        self.assertEqual(len(saved.lines), 1)
        self.assertIsNone(saved.purchase_order_id)

    def test_predictive_origin_does_not_create_an_order(self):
        from flask_login import login_user
        from app.services import replenishment_request_service

        before = self.counts()
        with self.app.test_request_context():
            login_user(db.session.get(User, self.users["inventario"].id))
            row = replenishment_request_service.create_from_selection({
                "notes": "Desde el pronóstico",
                "items": [{"product_id": self.product.id, "quantity": 3}],
            })
            self.assertEqual(row.origin, "predictive")
            self.assertEqual(row.status, "open")
            self.assert_no_money(row.to_dict())
            number = row.request_number
        self.assertEqual(self.counts()["po"], before["po"])
        self.assertEqual(self.counts()["stock"], before["stock"])
        self.assertEqual(self.counts()["moves"], before["moves"])
        seen = self.clients["asistente"].get("/api/replenishment-requests", query_string={"search": number})
        self.assertEqual(seen.get_json()["items"][0]["origin"], "predictive")


if __name__ == "__main__":
    unittest.main()
