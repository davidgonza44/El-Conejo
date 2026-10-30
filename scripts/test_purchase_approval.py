"""Approval, role separation and supply regression against disposable SQLite."""
import unittest
from datetime import datetime
from unittest.mock import patch

from approval_test_support import LinkedSupplierCase as IsolatedCase
from app.extensions import db
from app.models import StockMovement, Role, Permission
from app.models.purchase import PurchaseOrder
from app.services import purchase_service, replenishment_service, reception_query_service, rbac_service
from sqlalchemy.exc import SQLAlchemyError


class ApprovalTests(IsolatedCase):
    def create(self, actor="asistente"):
        today = datetime.now(purchase_service.inventory_service.COMMERCIAL_ZONE).date().isoformat()
        quote = self.clients["asistente"].post("/api/supplier-quotes", json={
            "supplier_id": self.supplier.id, "issued_on": today, "currency": "VES", "valid_until": "2099-01-01",
            "items": [{"product_id": self.product.id, "quantity": 4, "unit_price": "2.00"}]})
        self.assertEqual(quote.status_code, 201, quote.json)
        result = self.clients[actor].post("/api/purchases", json={"supplier_id": self.supplier.id,
            "supplier_quote_id": quote.json["id"],
            "items": [{"product_id": self.product.id, "quantity": 4}]})
        self.assertEqual(result.status_code, 201, result.json)
        return result.json

    def decision(self, order, action="approve", actor="admin", data=None):
        return self.clients[actor].post(f"/api/purchases/{order['id']}/{action}", json=data or {})

    def receive(self, order, quantity=4):
        return self.clients["inventario"].post(f"/api/purchases/{order['id']}/receptions", json={
            "received_at": datetime.now(purchase_service.inventory_service.COMMERCIAL_ZONE).date().isoformat(),
            "items": [{"line_id": order["items"][0]["id"], "quantity": quantity}]})

    def test_inventory_base_role_creates_and_cannot_decide(self):
        role = Role.query.filter_by(slug="asistente").one()
        self.assertIn("purchases:write", role.permission_codes)
        self.assertNotIn("purchases:approve", role.permission_codes)
        self.assertNotIn("purchases:write", Role.query.filter_by(slug="inventario").one().permission_codes)
        order = self.create()
        self.assertEqual(order["status"], "pending_approval")
        self.assertEqual(order["creator"]["id"], self.users["asistente"].id)
        self.assertEqual(self.stock(), 10)

        from app.services import supplier_query_service
        self.assertEqual(supplier_query_service.kpis()["pending"], 1)
        self.assertEqual(StockMovement.query.count(), 0)
        for action in ("approve", "reject"):
            self.assertEqual(self.decision(order, action, "inventario", {"reason":"No"} if action == "reject" else {}).status_code, 403)
        self.assertEqual(self.clients["inventario"].get("/purchases").status_code, 200)
        self.assertEqual(self.clients["vendedor"].get("/api/purchases").status_code, 403)

    def test_admin_cannot_approve_or_reject_own_order(self):
        order = self.create()
        # Historical self-created order: no new operational creation by admin.
        saved = db.session.get(PurchaseOrder, order["id"])
        saved.created_by_user_id = self.users["admin"].id
        db.session.commit()
        for action in ("approve", "reject"):
            self.assertEqual(self.decision(order, action, data={"reason":"No"} if action == "reject" else {}).status_code, 403)
        self.assertEqual(self.decision(order, actor="admin2").status_code, 200)

    def test_approval_stock_supply_and_receipt(self):
        order = self.create()
        self.assertEqual(self.receive(order).status_code, 409)
        self.assertEqual(replenishment_service.pending_supply_by_product(), {})
        self.assertEqual(reception_query_service.eligible().count(), 0)
        result = self.decision(order)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json["approved_by_user_id"], self.users["admin"].id)
        self.assertIsNotNone(result.json["approved_at"])
        self.assertEqual(self.stock(), 10)
        self.assertEqual(StockMovement.query.count(), 0)
        self.assertEqual(replenishment_service.pending_supply_by_product(), {self.product.id: 4})
        self.assertEqual(reception_query_service.eligible().count(), 1)
        self.assertEqual(self.decision(order).status_code, 409)
        self.assertEqual(self.decision(order, "reject", data={"reason":"No"}).status_code, 409)
        closed = self.receive(order, 1)
        self.assertEqual(closed.json["status"], "closed_partial")
        self.assertEqual(closed.json["items"][0]["quantity_not_supplied"], 3)
        self.assertEqual(self.stock(), 11)
        self.assertEqual(StockMovement.query.count(), 1)
        self.assertEqual(self.receive(order, 1).status_code, 409)

    def test_rejection_reason_and_terminal_state(self):
        order = self.create()
        for reason in (None, "", "   ", "x" * 1001, False):
            self.assertEqual(self.decision(order, "reject", data={"reason":reason}).status_code, 400)
        result = self.decision(order, "reject", data={"reason":"Sin presupuesto"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["rejection_reason"], "Sin presupuesto")
        self.assertEqual(result.json["rejected_by_user_id"], self.users["admin"].id)
        self.assertIsNotNone(result.json["rejected_at"])
        self.assertEqual(self.receive(order).status_code, 409)
        self.assertEqual(self.decision(order).status_code, 409)
        self.assertEqual(self.stock(), 10)
        from app.services import supplier_query_service
        self.assertEqual(supplier_query_service.kpis()["pending"], 0)
        self.assertEqual(supplier_query_service.kpis()["totals_by_currency"], [])

    def test_legacy_pending_requires_explicit_review_without_backfill(self):
        order = self.create()
        saved = db.session.get(PurchaseOrder, order["id"])
        saved.status = "pending"
        db.session.commit()
        result = self.receive(order)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.stock(), 10)
        self.assertIsNone(saved.approved_at)

    def test_edit_cancel_duplicate_and_kpis(self):
        order = self.create()
        path = f"/api/purchases/{order['id']}"
        editor = self.clients["asistente"]
        self.assertEqual(editor.patch(path, json={"notes":"Editada"}).status_code, 200)
        self.assertEqual(self.clients["admin"].get("/api/purchases").json["kpis"]["pending"], 1)
        self.assertEqual(self.decision(order).status_code, 200)
        self.assertEqual(editor.patch(path, json={"notes":"Solo metadatos"}).status_code, 409)
        duplicate = editor.post(path + "/duplicate", json={})
        self.assertEqual(duplicate.json["status"], "pending_approval")
        self.assertIsNone(duplicate.json["approved_at"])
        self.assertEqual(self.clients["admin"].post(path + "/cancel", json={"reason": "Error de captura"}).status_code, 200)
        self.assertEqual(self.receive(order).status_code, 409)

    def test_server_fields_permissions_and_rollback(self):
        order = self.create()
        for fields in ({"status":"approved"}, {"approved_by_user_id":1}):
            self.assertEqual(self.clients["asistente"].patch(f"/api/purchases/{order['id']}", json=fields).status_code, 400)
        with patch.object(db.session, "commit", side_effect=SQLAlchemyError("private detail")):
            result = self.decision(order)
        self.assertEqual(result.status_code, 409)
        self.assertNotIn("private", str(result.json))
        self.assertEqual(db.session.get(PurchaseOrder, order["id"]).status, "pending_approval")
        role = Role.query.filter_by(slug="admin").one()
        role.permissions.remove(Permission.query.filter_by(code="purchases:approve").one())
        db.session.commit(); rbac_service.invalidate_cache()
        self.assertEqual(self.decision(order).status_code, 403)

    def test_synthetic_seed_purchase_and_receipt_segment(self):
        from types import SimpleNamespace
        from flask_login import login_user
        from app import models
        from seed_tesis import purchase, receive

        domain = SimpleNamespace(db=db, models=models, purchase_service=purchase_service)
        when = datetime.utcnow()
        today = datetime.now(purchase_service.inventory_service.COMMERCIAL_ZONE).date()
        event = {"family":0, "day":today, "items":[(0, 4)]}
        with self.app.test_request_context():
            login_user(self.users["asistente"])
            order = purchase(domain, event, {0:self.product}, {0:self.supplier}, when)
            self.assertEqual(order.status, "pending_approval")
            login_user(self.users["admin"])
            purchase_service.decide_order(order.id, {}, approve=True)
            login_user(self.users["inventario"])
            receive(domain, event, order, {0:self.product}, when)
            self.assertEqual(order.status, "closed_complete")
            self.assertNotEqual(order.approved_by_user_id, order.created_by_user_id)
        self.assertEqual(self.stock(), 14)


if __name__ == "__main__":
    unittest.main(verbosity=2)
