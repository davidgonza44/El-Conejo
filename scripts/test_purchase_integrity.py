"""Regresiones de Compras con SQLite en memoria, sin conexiones ni entregas externas."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from test_purchase_tutor_20261005 import TutorFlowCase
from app.extensions import db
from app.models import Product, StockMovement
from app.models.purchase import PurchaseOrder, PurchaseReception, ProductSupplier
from app.models.supplier_quote import SupplierQuote
from app.models.purchase_correction import PurchaseReceptionCorrection
from app.services import purchase_export_service


class PurchaseIntegrityTests(TutorFlowCase):
    def direct_quote(self, quantity=1):
        response = self.clients["asistente"].post("/api/supplier-quotes", json={
            "supplier_id": self.supplier.id, "currency": "VES", "issued_on": self.today,
            "valid_until": "2099-01-01", "items": [{"product_id": self.product.id, "quantity": quantity, "unit_price": "10.00"}]})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def closed(self, quantities=None):
        order = self.approved()
        response = self.receive(order, quantities or [10], confirm_partial_closure=True, closure_reason="Error de conteo documentado en la entrega")
        self.assertEqual(response.status_code, 200, response.json)
        order = response.json
        return order, order["receptions"][0]["id"]

    def correction(self, receipt_id, line_id, quantity):
        response = self.clients["inventario"].post(f"/api/purchases/receptions/{receipt_id}/corrections", json={
            "reason": "Corrección de conteo comprobada con el proveedor", "items": [{"line_id": line_id, "quantity": quantity}]})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def decision(self, proposal, approve=True, **data):
        action = "approve" if approve else "reject"
        return self.clients["admin"].post(f"/api/purchases/receptions/{proposal['reception_id']}/corrections/{proposal['id']}/{action}", json=data)

    def test_quote_quantity_checked_before_preparing_and_renewing(self):
        row = self.request()
        self.start(row)
        quote = self.direct_quote(1)
        response = self.clients["asistente"].post(f"/api/replenishment-requests/{row['id']}/prepare", json={"supplier_quote_id": quote["id"]})
        self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(PurchaseOrder.query.count(), 0)
        html = self.clients["asistente"].get(f"/purchases/requests/{row['id']}").text
        self.assertNotIn(f'<option value="{quote["id"]}">CP-', html)
        order = self.draft()
        response = self.clients["asistente"].patch(f"/api/purchases/{order['id']}", json={"supplier_quote_id": quote["id"]})
        self.assertEqual(response.status_code, 400, response.json)

    def test_quote_quantity_rechecked_at_approval(self):
        order = self.draft()
        quote = db.session.get(SupplierQuote, order["supplier_quote_id"])
        quote.lines[0].quantity = 1
        db.session.commit()
        response = self.clients["admin"].post(f"/api/purchases/{order['id']}/approve", json={})
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(self.stock(), 10)

    def test_services_rejected_before_request_or_quote_exists(self):
        self.product.is_service = True
        db.session.commit()
        body = {"items": [{"product_id": self.product.id, "quantity": 2}]}
        self.assertEqual(self.clients["inventario"].post("/api/replenishment-requests", json=body).status_code, 400)
        body.update(supplier_id=self.supplier.id, currency="VES", issued_on=self.today, valid_until="2099-01-01")
        body["items"][0]["unit_price"] = "2.00"
        self.assertEqual(self.clients["asistente"].post("/api/supplier-quotes", json=body).status_code, 400)

    def supplier_body(self, name):
        return dict(name=name, tax_id="TEST-SUPPLIER", contact_name="Contacto", phone="0000000000", email="test@example.invalid", category="Pruebas", is_active=True)

    def test_supplier_snapshot_survives_approved_changes_and_receipt(self):
        order = self.approved()
        before = order["supplier"]
        proposed = self.clients["asistente"].patch(f"/api/suppliers/{self.supplier.id}", json=self.supplier_body("Nombre nuevo"))
        self.assertEqual(proposed.status_code, 200, proposed.json)
        approved = self.clients["admin"].post(f"/api/suppliers/{self.supplier.id}/approve", json={})
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertEqual(self.order(order["id"])["supplier"], before)
        printed = self.clients["admin"].get(f"/purchases/{order['id']}/print").text
        self.assertIn(before["name"], printed)
        self.assertNotIn("Nombre nuevo", printed)
        received = self.receive(order, [10]).json
        receipt_id = received["receptions"][0]["id"]
        receipt = self.clients["admin"].get(f"/api/purchases/receptions/{receipt_id}")
        self.assertEqual(receipt.json["supplier"]["name"], before["name"])

    def test_historical_snapshot_not_invented(self):
        order = self.approved()
        db.session.get(PurchaseOrder, order["id"]).supplier_snapshot = None
        db.session.commit()
        data = self.order(order["id"])
        self.assertFalse(data["supplier_snapshot_available"])
        self.assertIn("sin copia", self.clients["admin"].get(f"/purchases/{order['id']}/print").text)

    def test_pending_supplier_change_visible_in_queue_counts_and_dashboard(self):
        response = self.clients["asistente"].patch(f"/api/suppliers/{self.supplier.id}", json=self.supplier_body("Cambio pendiente"))
        self.assertEqual(response.status_code, 200, response.json)
        rows = self.clients["admin"].get("/api/suppliers?approval_status=pending").json
        self.assertEqual(rows["kpis"]["pending_approvals"], 1)
        self.assertTrue(rows["items"][0]["pending_change"])
        inbox = self.clients["admin"].get("/api/purchases/approvals")
        self.assertEqual(inbox.status_code, 200, inbox.json)
        self.assertEqual(inbox.json["counts"]["supplier_change"], 1)
        self.assertEqual(self.clients["inventario"].get("/api/purchases/approvals").status_code, 403)
        page = self.clients["admin"].get("/purchases/approvals")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Cambio de proveedor", page.text)
        from app.services.dashboard_service import pending_proposals
        with self.app.test_request_context():
            counts = pending_proposals("admin")
            self.assertEqual(next(row["count"] for row in counts if row["label"] == "Proveedores pendientes"), 1)

    def test_supplier_status_requires_reason_and_another_role(self):
        path = f"/api/suppliers/{self.supplier.id}/status-proposals"
        body = {"is_active": False, "reason": "No suministra productos según el acuerdo"}
        self.assertEqual(self.clients["admin"].post(path, json=body).status_code, 403)
        self.assertEqual(self.clients["asistente"].post(path, json={"is_active": False}).status_code, 400)
        proposal = self.clients["asistente"].post(path, json=body)
        self.assertEqual(proposal.status_code, 200, proposal.json)
        self.assertTrue(proposal.json["is_active"])
        self.assertEqual(self.clients["asistente"].post(f"/api/suppliers/{self.supplier.id}/approve", json={}).status_code, 403)
        approved = self.clients["admin"].post(f"/api/suppliers/{self.supplier.id}/approve", json={})
        self.assertFalse(approved.json["is_active"])
        body["is_active"] = True
        self.assertEqual(self.clients["asistente"].post(path, json=body).status_code, 200)
        rejected = self.clients["admin"].post(f"/api/suppliers/{self.supplier.id}/reject", json={"reason": "Aún no cumple"})
        self.assertFalse(rejected.json["is_active"])
        self.assertEqual(rejected.json["changes_history"][0]["status"], "rejected")

    def test_receipt_cannot_predate_approval(self):
        order = self.approved()
        persisted = db.session.get(PurchaseOrder, order["id"])
        persisted.ordered_at = datetime.utcnow()-timedelta(days=3)
        db.session.commit()
        yesterday = (datetime.fromisoformat(self.today)-timedelta(days=1)).date().isoformat()
        before = self.counts()
        rejected = self.receive(order, [10], received_at=yesterday)
        self.assertEqual(rejected.status_code, 400, rejected.json)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.receive(order, [10]).status_code, 200)

    def test_correction_is_approved_atomic_and_original_receipt_is_immutable(self):
        order, receipt_id = self.closed()
        proposal = self.correction(receipt_id, order["items"][0]["id"], 7)
        self.assertEqual(self.stock(), 20)
        approved = self.decision(proposal)
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertEqual(self.stock(), 17)
        receipt = db.session.get(PurchaseReception, receipt_id)
        self.assertEqual(receipt.lines[0].quantity, 10)
        data = self.order(order["id"])
        self.assertEqual((data["items"][0]["quantity_received"], data["quantity_not_supplied"]), (7, 3))
        detail = self.clients["inventario"].get(f"/api/purchases/receptions/{receipt_id}").json
        self.assertEqual((detail["units"], detail["original_units"]), (7, 10))
        self.assertEqual(detail["movements"][-1]["source_type"], "purchase_reception_correction")
        self.assertEqual(self.decision(proposal).status_code, 409)
        self.assertEqual(self.stock(), 17)

    def test_increasing_correction_and_rejection_preserve_order_history(self):
        order, receipt_id = self.closed([6])
        proposal = self.correction(receipt_id, order["items"][0]["id"], 10)
        self.assertEqual(self.decision(proposal).status_code, 200)
        self.assertEqual(self.stock(), 20)
        self.assertEqual(self.order(order["id"])["status"], "closed_complete")
        next_proposal = self.correction(receipt_id, order["items"][0]["id"], 9)
        self.assertEqual(self.decision(next_proposal, False, reason="El conteo original está confirmado").status_code, 200)
        self.assertEqual(self.stock(), 20)

    def test_correction_stock_changed_or_insufficient_is_rejected(self):
        order, receipt_id = self.closed()
        proposal = self.correction(receipt_id, order["items"][0]["id"], 7)
        from app.services import inventory_service
        from flask_login import login_user
        with self.app.test_request_context():
            login_user(self.users["inventario"])
            inventory_service.register_exit({"product_id": self.product.id, "quantity": 1})
        self.assertEqual(self.decision(proposal).status_code, 409)
        self.assertEqual(self.stock(), 19)
        self.assertEqual(self.decision(proposal, False, reason="Recalcular tras la salida").status_code, 200)
        self.product.current_stock = 1
        db.session.commit()
        proposal = self.correction(receipt_id, order["items"][0]["id"], 0)
        self.assertEqual(self.decision(proposal).status_code, 409)
        self.assertEqual(self.stock(), 1)
        self.assertEqual(PurchaseReceptionCorrection.query.filter_by(status="approved").count(), 0)

    def test_correction_and_shortage_are_mutually_exclusive(self):
        order, receipt_id = self.closed([6])
        proposal = self.correction(receipt_id, order["items"][0]["id"], 7)
        path = f"/api/replenishment-requests/from-order/{order['id']}"
        self.assertEqual(self.clients["inventario"].post(path, json={}).status_code, 409)
        self.assertEqual(self.decision(proposal, False, reason="Tramitar faltante").status_code, 200)
        self.assertEqual(self.clients["inventario"].post(path, json={}).status_code, 201)
        response = self.clients["inventario"].post(f"/api/purchases/receptions/{receipt_id}/corrections", json={
            "reason": "Corrección posterior", "items": [{"line_id": order["items"][0]["id"], "quantity": 7}]})
        self.assertEqual(response.status_code, 409)

    def test_correction_authorization_csrf_and_duplicate(self):
        order, receipt_id = self.closed()
        path = f"/api/purchases/receptions/{receipt_id}/corrections"
        body = {"reason": "Conteo confirmado", "items": [{"line_id": order["items"][0]["id"], "quantity": 7}]}
        self.assertEqual(self.clients["asistente"].post(path, json=body).status_code, 403)
        self.assertEqual(self.clients["inventario"].post(path, json=body, headers={"X-CSRFToken": "invalid"}).status_code, 400)
        proposal = self.clients["inventario"].post(path, json=body)
        self.assertEqual(proposal.status_code, 201)
        self.assertEqual(self.clients["inventario"].post(path, json=body).status_code, 409)
        p = proposal.json
        self.assertEqual(self.clients["inventario"].post(path+f"/{p['id']}/approve", json={}).status_code, 403)

    def test_stage_totals_do_not_confuse_drafts_received_and_not_supplied(self):
        self.draft()
        order, receipt_id = self.closed([6])
        result = self.clients["admin"].get("/api/purchases?start=&end=").json["kpis"]["stage_totals"]
        self.assertEqual({key: rows[0]["amount"] for key, rows in result.items()},
                         {"requested":"200.00", "approved":"100.00", "received":"60.00", "not_supplied":"40.00"})
        hidden = self.clients["inventario"].get("/api/purchases?start=&end=").json["kpis"]["stage_totals"]
        self.assertTrue(all(not rows for rows in hidden.values()))

    def test_exports_are_bounded_before_formatting(self):
        order, receipt_id = self.closed()
        with patch.object(purchase_export_service, "MAX_EXPORT_ROWS", 0):
            for path in ("/api/purchases/export.csv?start=&end=", "/api/suppliers/export", "/api/purchases/receptions/export"):
                response = self.clients["admin"].get(path)
                self.assertEqual(response.status_code, 400, (path, response.json))
                self.assertIn("Aplique filtros", response.json["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
