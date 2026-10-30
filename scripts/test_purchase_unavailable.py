"""Regresiones de vacancia: SQLite desechable, sin dotenv ni MySQL."""
import unittest
from datetime import datetime

from approval_test_support import LinkedSupplierCase as IsolatedCase
from app.extensions import db
from app.models import StockMovement
from app.models.purchase import PurchaseOrder, PurchaseReception, Supplier
from app.models.purchase_closure import PurchaseClosureRequest
from app.services.inventory_service import COMMERCIAL_ZONE


class UnavailableTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.today = datetime.now(COMMERCIAL_ZONE).date().isoformat()

    def call(self, actor, method, path, status=200, data=None):
        response = self.clients[actor].open(path, method=method, json=data)
        self.assertEqual(response.status_code, status, response.get_data(as_text=True)[:300])
        return response.get_json()

    def order(self, approve=True, supplier=None, price="7.25", quantity=5):
        supplier = supplier or self.supplier
        quote = self.call("asistente", "POST", "/api/supplier-quotes", 201, {
            "supplier_id": supplier.id, "issued_on": self.today, "valid_until": "2099-01-01",
            "currency": "VES", "items": [{"product_id": self.product.id,
                                         "quantity": quantity, "unit_price": price}]})
        order = self.call("asistente", "POST", "/api/purchases", 201, {
            "supplier_id": supplier.id, "supplier_quote_id": quote["id"],
            "items": [{"product_id": self.product.id, "quantity": quantity}]})
        if approve:
            order = self.call("admin", "POST", f"/api/purchases/{order['id']}/approve", data={})
        return order

    def request(self, order, status=200, reason="El proveedor confirma que no despachará."):
        return self.call("asistente", "POST", f"/api/purchases/{order['id']}/closure-requests",
                         status, {"kind": "unavailable", "reason": reason})

    def decide(self, order, action="approve", status=200, actor="admin", data=None):
        return self.call(actor, "POST", f"/api/purchases/{order['id']}/closure-requests/{action}",
                         status, data if data is not None else ({} if action == "approve" else {"reason": "Se mantiene la compra."}))

    def receive(self, order, quantity=5, status=200):
        return self.call("inventario", "POST", f"/api/purchases/{order['id']}/receptions", status,
                         {"received_at": self.today, "items": [{"line_id": order["items"][0]["id"], "quantity": quantity}]})

    def original(self, order):
        db.session.expire_all()
        row = db.session.get(PurchaseOrder, order["id"])
        return (row.supplier_id, row.subtotal, row.total, row.supplier_quote_id,
                [(line.product_id, line.product_code, line.product_name, line.quantity_ordered,
                  line.unit_cost, line.line_total) for line in row.lines])

    def test_close_preserves_stock_receipts_movements_prices_and_trace(self):
        order = self.order()
        original = self.original(order)
        stock, cost = self.stock(), self.product.purchase_price
        counts = (StockMovement.query.count(), PurchaseReception.query.count())
        requested = self.request(order)
        self.assertEqual(requested["status"], "approved")
        final = self.decide(order)
        self.assertEqual(final["status"], "closed_unavailable")
        self.assertEqual(self.stock(), stock)
        self.assertEqual(self.product.purchase_price, cost)
        self.assertEqual((StockMovement.query.count(), PurchaseReception.query.count()), counts)
        self.assertEqual(self.original(order), original)
        trace = PurchaseClosureRequest.query.filter_by(purchase_order_id=order["id"]).one()
        self.assertEqual(trace.requested_by_user_id, self.users["asistente"].id)
        self.assertEqual(trace.decided_by_user_id, self.users["admin"].id)
        self.assertEqual(trace.status, "approved")
        self.assertTrue(trace.reason and trace.requested_at and trace.decided_at)
        self.assertLessEqual(trace.requested_at, trace.decided_at)
        self.assertEqual(final["closed_at"], final["closure_request"]["decided_at"])

    def test_reject_keeps_approved_unchanged_and_receivable(self):
        order = self.order()
        original, stock = self.original(order), self.stock()
        self.request(order)
        final = self.decide(order, "reject")
        self.assertEqual(final["status"], "approved")
        self.assertEqual(final["closure_request"]["status"], "rejected")
        self.assertEqual(self.original(order), original)
        self.assertEqual(self.stock(), stock)
        self.assertEqual(PurchaseReception.query.count(), 0)
        self.assertEqual(self.receive(order)["status"], "closed_complete")
        self.assertEqual(self.stock(), stock + 5)

    def test_self_approval_and_other_roles_forbidden(self):
        order = self.order()
        self.request(order)
        self.decide(order, status=403, actor="asistente")
        for actor in ("inventario", "vendedor"):
            self.decide(order, status=403, actor=actor)
            self.call(actor, "POST", f"/api/purchases/{order['id']}/closure-requests", 403,
                      {"kind": "unavailable", "reason": "No disponible"})
        # Even a historical request whose requester is now admin cannot be self-decided.
        row = PurchaseClosureRequest.query.one()
        row.requested_by_user_id = self.users["admin"].id
        db.session.commit()
        for action in ("approve", "reject"):
            self.decide(order, action, 403)
        self.decide(order, actor="admin2")

    def test_invalid_reason_and_decision_fields_are_client_errors(self):
        order = self.order()
        for reason in (None, "", " \n\t ", 12, [], "x" * 1001):
            with self.subTest(reason=repr(reason)[:25]):
                self.request(order, 400, reason)
        self.assertEqual(PurchaseClosureRequest.query.count(), 0)
        self.request(order)
        for data in ({"supplier_id": self.supplier.id}, {"items": []}, {"unit_cost": "99"}, {"quantity": 99}):
            self.decide(order, status=400, data=data)
        self.decide(order, "reject", 400, data={"reason": " "})
        self.decide(order)

    def test_only_approved_without_any_receipt_can_request(self):
        for status in ("pending_approval", "rejected", "closed_complete", "closed_partial",
                       "closed_price_change", "cancelled", "pending", "partial", "completed"):
            with self.subTest(status=status):
                order = self.order(False)
                db.session.get(PurchaseOrder, order["id"]).status = status
                db.session.commit()
                self.request(order, 409)

    def test_decision_also_rechecks_invalid_order_status(self):
        for status in ("pending_approval", "rejected", "closed_complete", "closed_partial",
                       "closed_price_change", "cancelled"):
            with self.subTest(status=status):
                order = self.order()
                self.request(order)
                db.session.get(PurchaseOrder, order["id"]).status = status
                db.session.commit()
                self.decide(order, status=409)
                self.assertEqual(self.stock(), 10)

    def test_closed_order_cannot_edit_receive_cancel_or_close_again(self):
        order = self.order()
        original = self.original(order)
        self.request(order)
        self.request(order, 409)
        self.decide(order)
        for changes in ({"supplier_id": self.supplier.id}, {"items": []}, {"notes": "cambio"},
                        {"items": [{"product_id": self.product.id, "quantity": 99, "unit_cost": "99.00"}]}):
            self.call("asistente", "PATCH", f"/api/purchases/{order['id']}", 409, changes)
        self.receive(order, status=409)
        self.request(order, 409)
        for action in ("approve", "reject"):
            self.decide(order, action, 409)
        self.call("admin", "POST", f"/api/purchases/{order['id']}/cancel", 409, {"reason": "Intento"})
        self.assertEqual(self.original(order), original)

    def test_second_decision_after_rejection_conflicts(self):
        order = self.order()
        self.request(order)
        self.decide(order, "reject")
        self.decide(order, status=409)
        self.decide(order, "reject", 409)

    def test_unavailable_cannot_bypass_new_sr_and_quote_by_duplication(self):
        order = self.order()
        self.request(order)
        self.decide(order)
        self.call("asistente", "POST", f"/api/purchases/{order['id']}/duplicate", 409, {})
        self.assertEqual(PurchaseOrder.query.count(), 1)

    def test_missing_order_is_404(self):
        self.request({"id": 2147483647}, 404)
        self.decide({"id": 2147483647}, status=404)

    def test_partial_receipt_is_never_unavailable(self):
        order = self.order()
        partial = self.receive(order, 2)
        self.assertEqual(partial["status"], "closed_partial")
        self.assertEqual(partial["items"][0]["quantity_not_supplied"], 3)
        self.request(order, 409)
        self.decide(order, status=409)

    def test_receipt_winner_leaves_no_undecidable_pending_closure(self):
        order = self.order()
        self.request(order)
        final = self.receive(order, 2)
        self.assertEqual(final["status"], "closed_partial")
        self.assertEqual(final["closure_request"]["status"], "cancelled")
        self.assertIsNone(final["closure_request"]["decided_by_user_id"])
        self.assertIn("recepción", final["closure_request"]["decision_note"])
        self.decide(order, status=409)

    def test_new_need_uses_new_sr_quote_supplier_order_and_approval(self):
        first = self.order()
        self.request(first)
        self.decide(first)
        original = self.original(first)
        supplier = Supplier(name="Otro proveedor", tax_id="TEST-REPLACEMENT")
        db.session.add(supplier)
        db.session.commit()
        self.associate(self.product, supplier)
        request = self.call("inventario", "POST", "/api/replenishment-requests", 201,
                            {"items": [{"product_id": self.product.id, "quantity": 3}]})
        path = f"/api/replenishment-requests/{request['id']}"
        self.call("asistente", "POST", path + "/start", data={})
        quote = self.call("asistente", "POST", path + "/quotes", 201, {
            "supplier_id": supplier.id, "issued_on": self.today, "valid_until": "2099-01-01",
            "currency": "VES", "items": [{"product_id": self.product.id, "unit_price": "9.50"}]})
        prepared = self.call("asistente", "POST", path + "/prepare", data={"supplier_quote_id": quote["id"]})
        second_id = prepared["purchase_order_id"]
        second = self.call("asistente", "GET", f"/api/purchases/{second_id}")
        self.assertNotEqual(second["order_number"], first["order_number"])
        self.assertEqual(second["status"], "pending_approval")
        self.assertEqual(second["items"][0]["unit_cost"], "9.50")
        self.assertEqual(second["supplier"]["id"], supplier.id)
        self.call("admin", "POST", f"/api/purchases/{second_id}/approve", data={})
        self.assertEqual(self.original(first), original)
        self.assertEqual(db.session.get(PurchaseOrder, first["id"]).status, "closed_unavailable")

    def test_pending_closure_filter_excludes_other_orders_and_preserves_pagination(self):
        pending = self.order()
        self.order()
        self.request(pending)
        page = self.call("admin", "GET", "/api/purchases?closure_status=pending&start=&end=")
        self.assertEqual([row["id"] for row in page["items"]], [pending["id"]])
        self.assertEqual(page["pagination"]["total"], 1)
        html = self.clients["admin"].get("/purchases?start=&end=").get_data(as_text=True)
        self.assertIn("Cierres pendientes", html)
        self.assertIn('name="closure_status"', html)
        self.decide(pending)
        self.assertEqual(self.call("admin", "GET", "/api/purchases?closure_status=pending&start=&end=")["items"], [])


if __name__ == "__main__":
    unittest.main()
