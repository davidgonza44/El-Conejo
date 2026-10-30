"""Ciclo del tutor: roles, solicitud, aprobación, cierre de OC y demanda sin doble conteo.

SQLite en memoria. No abre MySQL, el puerto 5000 ni la base de la empresa.
"""
import unittest
from datetime import datetime

from approval_test_support import LinkedSupplierCase as IsolatedCase
from app.extensions import db
from app.models import Customer, DeliveryNote, DeliveryNoteItem, Product, Sale, SaleLine, StockMovement, User
from app.models.purchase import PurchaseOrder
from app.services.demand_data_service import TemporalCutoff, _load_operational_events
from app.utils.navigation import home_path, nav_allowed


class TutorCycleTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.today = datetime.utcnow().date().isoformat()

    def quote(self, price="10.00", valid_until="2099-01-01"):
        result = self.clients["asistente"].post("/api/supplier-quotes", json={
            "supplier_id": self.supplier.id, "issued_on": self.today, "currency": "VES",
            "valid_until": valid_until,
            "items": [{"product_id": self.product.id, "quantity": 10, "unit_price": price}]})
        self.assertEqual(result.status_code, 201, result.get_json())
        return result.get_json()

    def order(self, quote, quantity=10):
        result = self.clients["asistente"].post("/api/purchases", json={
            "supplier_id": self.supplier.id, "supplier_quote_id": quote["id"],
            "items": [{"product_id": self.product.id, "quantity": quantity}]})
        self.assertEqual(result.status_code, 201, result.get_json())
        return result.get_json()

    def approve(self, order):
        result = self.clients["admin"].post(f"/api/purchases/{order['id']}/approve", json={})
        self.assertEqual(result.status_code, 200, result.get_json())
        return result.get_json()

    def test_roles_and_money_are_not_visible_to_inventory(self):
        self.assertEqual(home_path("asistente"), "/dashboard")
        self.assertFalse(nav_allowed("vendedor", "sales_new"))
        self.assertFalse(nav_allowed("vendedor", "sales_orders"))
        self.assertFalse(nav_allowed("vendedor", "sales_quotes"))
        inventory = self.clients["inventario"]
        self.assertEqual(inventory.post("/api/purchases", json={}).status_code, 403)
        self.assertEqual(inventory.post("/api/products", json={"name": "X"}).status_code, 403)
        self.assertEqual(inventory.post("/api/suppliers", json={"name": "X"}).status_code, 403)
        product = inventory.get(f"/api/products/{self.product.id}").get_json()
        self.assertNotIn("purchase_price", product)
        self.assertNotIn("sale_price", product)
        self.assertIsNone(inventory.get("/api/purchases").get_json()["kpis"]["total"])
        export = inventory.post("/api/products/export", json={"format": "csv", "options": {"include_stock_prices": True}})
        self.assertEqual(export.status_code, 200, export.get_data(as_text=True)[:300])
        self.assertNotIn("Precio", export.get_data(as_text=True))
        purchases = inventory.get("/api/purchases/export.csv")
        self.assertNotIn("Monto", purchases.get_data(as_text=True))
        chat = inventory.post("/api/chatbot/message", json={"message": "Precio de Producto sintético"})
        self.assertEqual(chat.status_code, 403)
        page = inventory.get("/chatbot")
        self.assertIn("Asistente de Inventario", page.get_data(as_text=True))
        self.assertIn("Asistente Administrativo", self.clients["asistente"].get("/chatbot").get_data(as_text=True))
        self.assertIn("Asistente de Administración", self.clients["admin"].get("/chatbot").get_data(as_text=True))
        login = self.app.test_client().get("/login")
        self.assertIn("logo_oficial_el_conejo.png", login.get_data(as_text=True))

    def test_product_and_supplier_need_approval_and_oc_has_no_inline_supplier(self):
        category = self.product.category_id
        proposed = self.clients["asistente"].post("/api/products", json={
            "code": "NEW-1", "name": "Producto propuesto", "category_id": category,
            "unit": "unidad", "purchase_price": "1.00", "sale_price": "2.00", "is_active": True})
        self.assertEqual(proposed.status_code, 201, proposed.get_json())
        self.assertEqual(proposed.get_json()["approval_status"], "pending")
        self.assertFalse(proposed.get_json()["is_active"])
        self.assertEqual(self.clients["asistente"].post(f"/api/products/{proposed.get_json()['id']}/approve").status_code, 403)
        approved = self.clients["admin"].post(f"/api/products/{proposed.get_json()['id']}/approve")
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.get_json()["approval_status"], "approved")
        supplier = self.clients["asistente"].post("/api/suppliers", json={
            "name": "Proveedor nuevo", "tax_id": "J-1", "contact_name": "Ana", "phone": "0212",
            "email": "ana@example.invalid", "category": "Ferretería", "is_active": True})
        self.assertEqual(supplier.status_code, 201, supplier.get_json())
        self.assertEqual(supplier.get_json()["approval_status"], "pending")
        self.assertEqual(self.clients["asistente"].post(f"/api/suppliers/{supplier.get_json()['id']}/approve").status_code, 403)
        before = [row["id"] for row in self.clients["asistente"].get("/api/purchases/options").get_json()["suppliers"]]
        self.assertNotIn(supplier.get_json()["id"], before)
        self.assertEqual(self.clients["admin"].post(f"/api/suppliers/{supplier.get_json()['id']}/approve", json={}).status_code, 200)
        self.assertEqual(self.clients["asistente"].post("/api/purchases/suppliers", json={"name": "Inline"}).status_code, 403)
        listed = [row["id"] for row in self.clients["asistente"].get("/api/purchases/options").get_json()["suppliers"]]
        self.assertIn(supplier.get_json()["id"], listed)

    def test_partial_receipt_closes_and_a_new_need_creates_another_order(self):
        first = self.approve(self.order(self.quote(), quantity=10))
        received = self.clients["inventario"].post(f"/api/purchases/{first['id']}/receptions", json={
            "received_at": self.today, "items": [{"line_id": first["items"][0]["id"], "quantity": 6}]})
        self.assertEqual(received.status_code, 200, received.get_json())
        self.assertEqual(received.get_json()["status"], "closed_partial")
        self.assertEqual(received.get_json()["items"][0]["quantity_ordered"], 10)
        self.assertEqual(received.get_json()["items"][0]["quantity_received"], 6)
        self.assertEqual(received.get_json()["items"][0]["quantity_not_supplied"], 4)
        self.assertNotIn("unit_cost", self.clients["inventario"].get(f"/api/purchases/{first['id']}").get_json()["items"][0])
        again = self.clients["inventario"].post(f"/api/purchases/{first['id']}/receptions", json={
            "received_at": self.today, "items": [{"line_id": first["items"][0]["id"], "quantity": 4}]})
        self.assertEqual(again.status_code, 409)
        self.assertEqual(self.clients["asistente"].patch(f"/api/purchases/{first['id']}", json={"notes": "cambio"}).status_code, 409)
        request = self.clients["inventario"].post("/api/replenishment-requests", json={
            "items": [{"product_id": self.product.id, "quantity": 4}]})
        self.assertEqual(request.status_code, 201, request.get_json())
        self.assertNotIn("unit_cost", request.get_json()["items"][0])
        second = self.order(self.quote(price="12.00"), quantity=4)
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(second["status"], "pending_approval")
        self.assertEqual(db.session.get(PurchaseOrder, first["id"]).lines[0].unit_cost, 10)

    def test_price_change_and_unavailability_need_admin_and_then_a_new_order(self):
        price = self.approve(self.order(self.quote()))
        asked = self.clients["asistente"].post(f"/api/purchases/{price['id']}/closure-requests", json={
            "kind": "price_change", "reason": "El proveedor informó 12"})
        self.assertEqual(asked.status_code, 200, asked.get_json())
        self.assertEqual(asked.get_json()["status"], "approved")
        self.assertEqual(self.clients["asistente"].post(
            f"/api/purchases/{price['id']}/closure-requests/approve", json={}).status_code, 403)
        closed = self.clients["admin"].post(f"/api/purchases/{price['id']}/closure-requests/approve", json={})
        self.assertEqual(closed.status_code, 200, closed.get_json())
        self.assertEqual(closed.get_json()["status"], "closed_price_change")
        self.assertIn("12", closed.get_json()["closure_reason"])
        self.assertEqual(self.clients["inventario"].post(f"/api/purchases/{price['id']}/receptions", json={
            "received_at": self.today, "items": [{"line_id": price["items"][0]["id"], "quantity": 1}]}).status_code, 409)
        replacement = self.approve(self.order(self.quote(price="12.00")))
        self.assertEqual(replacement["status"], "approved")
        missing = self.approve(self.order(self.quote()))
        self.clients["asistente"].post(f"/api/purchases/{missing['id']}/closure-requests", json={
            "kind": "unavailable", "reason": "El proveedor no tiene existencia"})
        denied = self.clients["asistente"].post(f"/api/purchases/{missing['id']}/closure-requests/approve", json={})
        self.assertEqual(denied.status_code, 403)
        unavailable = self.clients["admin"].post(f"/api/purchases/{missing['id']}/closure-requests/approve", json={})
        self.assertEqual(unavailable.get_json()["status"], "closed_unavailable")
        expired = self.clients["asistente"].post("/api/supplier-quotes", json={
            "supplier_id": self.supplier.id, "issued_on": "1999-01-01", "currency": "VES",
            "valid_until": "1999-12-31",
            "items": [{"product_id": self.product.id, "quantity": 1, "unit_price": "10.00"}]}).get_json()
        blocked = self.clients["asistente"].post("/api/purchases", json={
            "supplier_id": self.supplier.id, "supplier_quote_id": expired["id"],
            "items": [{"product_id": self.product.id, "quantity": 1}]})
        self.assertEqual(blocked.status_code, 400)

    def test_operational_demand_does_not_count_sale_and_dispatch_twice(self):
        user = self.users["inventario"]
        customer = Customer(name="Cliente sintético")
        movement = StockMovement(product_id=self.product.id, movement_type="salida", quantity=2,
                                 previous_stock=10, new_stock=8, reason="Salida de prueba", user_id=user.id)
        db.session.add_all([customer, movement])
        db.session.flush()
        when = datetime.utcnow()
        sale = Sale(customer_id=customer.id, seller_id=user.id, sold_at=when, payment_method="cash",
                    payment_condition="cash", subtotal=6, discount_total=0, total=6, status="completed")
        note = DeliveryNote(note_number="NE-TEST", customer_name="Cliente sintético", status="issued",
                            total_amount=6, created_by_user_id=user.id, created_at=when)
        db.session.add_all([sale, note])
        db.session.flush()
        db.session.add(SaleLine(sale_id=sale.id, product_id=self.product.id, stock_movement_id=movement.id,
                                product_code="TEST-01", product_name="Producto sintético", quantity=2,
                                unit_price=3, discount_percent=0, discount_amount=0, line_subtotal=6, line_total=6))
        db.session.add(DeliveryNoteItem(delivery_note_id=note.id, product_id=self.product.id,
                                        product_code="TEST-01", product_name="Producto sintético",
                                        quantity=2, unit_price=3, line_total=6))
        db.session.commit()
        cutoff = TemporalCutoff(False, 0, None, None, None)
        events = _load_operational_events(cutoff, product_id=self.product.id)
        self.assertEqual(len(events), 1)
        self.assertEqual(int(events[0].quantity), 2)


if __name__ == "__main__":
    unittest.main()
