"""Reconciliation API and atomic stock invariants; SQLite only, synthetic inputs."""
import unittest
from io import BytesIO
from unittest.mock import patch

from approval_test_support import IsolatedCase
from app.extensions import db
from app.models import Role, Permission, StockMovement
from app.models.reconciliation import ReconciliationRun, ReconciliationLine
from app.services import inventory_service, rbac_service, reconciliation_service
from sqlalchemy.exc import SQLAlchemyError


class ReconciliationTests(IsolatedCase):
    def upload(self, content=b"product_code;quantity;product_name\nTEST-01;14;Informative\n", actor="admin", **extra):
        return self.clients[actor].post("/api/reconciliation", data={"file": (BytesIO(content), extra.get("filename", "cut.csv")),
            "cutoff_at": extra.get("cutoff_at", "2026-09-28T12:00:00-04:00")}, content_type="multipart/form-data")

    def preview(self):
        result = self.upload()
        self.assertEqual(result.status_code, 201, result.json)
        return result.json

    def action(self, run, action="confirm", data=None, actor="admin", line=False):
        path = f"/api/reconciliation/{run['id']}"
        if line:
            path += f"/lines/{run['items'][0]['id']}"
        return self.clients[actor].post(path + "/" + action, json=data or {})

    def test_preview_matching_counts_and_no_stock(self):
        result = self.upload(b"product_code;quantity;product_name\nTEST-01;10;Different name\nUNKNOWN;3;Unknown\nBAD;-1;Invalid\n")
        self.assertEqual(result.status_code, 201, result.json)
        self.assertEqual([line["result"] for line in result.json["items"]], ["match", "unmatched", "invalid"])
        self.assertEqual(result.json["received_count"], 3)
        self.assertEqual(result.json["invalid_count"], 1)
        self.assertEqual(len(result.json["sha256"]), 64)
        self.assertEqual(self.stock(), 10)
        self.assertEqual(StockMovement.query.count(), 0)

    def test_invalid_structure_never_persists(self):
        for content in (b"", b"product_code,quantity\nTEST-01,1", b"product_code;quantity\nTEST-01", b"product_code;quantity\nA;1;extra", b"\xff", b"product_code;quantity;quantity\nA;1;2", b"product_code;quantity\n\x00;1"):
            self.assertEqual(self.upload(content).status_code, 400)
        for extra in ({"filename":"cut.xlsx"}, {"cutoff_at":"invalid"}, {"cutoff_at":"2026-09-28T12:00:00"}):
            self.assertEqual(self.upload(**extra).status_code, 400)
        self.assertEqual(ReconciliationRun.query.count(), 0)
        self.assertEqual(ReconciliationLine.query.count(), 0)
        self.assertEqual(self.stock(), 10)

    def test_limits_and_duplicate_rows(self):
        self.assertEqual(self.upload(b"a" * (reconciliation_service.MAX_BYTES + 1)).status_code, 400)
        with patch.object(reconciliation_service, "MAX_ROWS", 1):
            self.assertEqual(self.upload(b"product_code;quantity\nTEST-01;1\nB;2").status_code, 400)
        result = self.upload(b"product_code;quantity\nTEST-01;1\ntest-01;2")
        self.assertEqual(result.json["invalid_count"], 1)

    def test_confirmation_recalculates_current_snapshot(self):
        run = self.preview()
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(self.users["admin"])
            inventory_service.register_entry({"product_id":self.product.id, "quantity":2})
        result = self.action(run)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json["items"][0]["system_quantity"], 12)
        self.assertEqual(result.json["items"][0]["difference"], 2)
        self.assertEqual(self.stock(), 12)
        self.assertEqual(StockMovement.query.count(), 1)
        self.assertEqual(self.action(run).status_code, 409)

    def test_review_and_acknowledge_audit(self):
        run = self.preview()
        self.assertEqual(self.action(run, "review", {"note":"Conteo"}, line=True).status_code, 409)
        self.action(run)
        self.assertEqual(self.action(run, "review", {"note":"Conteo"}, line=True).status_code, 200)
        self.assertEqual(self.action(run, "review", {"note":"Otra vez"}, line=True).status_code, 409)
        result = self.action(run, "resolve", {"note":"Diferencia documentada", "action":"acknowledge"}, line=True)
        self.assertEqual(result.status_code, 200, result.json)
        row = result.json["items"][0]
        self.assertEqual(row["review_status"], "resolved")
        self.assertEqual(row["resolved_by_user_id"], self.users["admin"].id)
        self.assertIsNotNone(row["reviewed_at"])
        self.assertIsNotNone(row["resolved_at"])
        self.assertIsNone(row["stock_movement_id"])
        self.assertEqual(self.stock(), 10)
        self.assertEqual(self.action(run, "resolve", {"note":"Again", "action":"acknowledge"}, line=True).status_code, 409)

    def test_adjustment_explicit_atomic_and_linked(self):
        run = self.preview(); self.action(run)
        payload = {"note":"Conteo físico", "action":"adjustment"}
        self.assertEqual(self.action(run, "resolve", payload, line=True).status_code, 400)
        payload["new_stock"] = 13  # Intentionally different from the external quantity of 14.
        original = inventory_service.register_adjustment
        with patch.object(inventory_service, "register_adjustment", wraps=original) as adjust:
            result = self.action(run, "resolve", payload, line=True)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertFalse(adjust.call_args.kwargs["commit"])
        self.assertEqual(self.stock(), 13)
        movement = db.session.get(StockMovement, result.json["items"][0]["stock_movement_id"])
        self.assertEqual(movement.movement_type, "ajuste")
        self.assertEqual(movement.previous_stock, 10)
        self.assertEqual(movement.new_stock, 13)

    def test_failed_commit_rolls_back_stock_movement_and_resolution(self):
        run = self.preview(); self.action(run)
        with patch.object(db.session, "commit", side_effect=SQLAlchemyError("private detail")):
            result = self.action(run, "resolve", {"note":"Conteo", "action":"adjustment", "new_stock":13}, line=True)
        self.assertEqual(result.status_code, 409)
        self.assertNotIn("private", str(result.json))
        self.assertEqual(self.stock(), 10)
        self.assertEqual(StockMovement.query.count(), 0)
        self.assertEqual(db.session.get(ReconciliationLine, run["items"][0]["id"]).review_status, "pending")

    def test_permissions_and_adjustment_extra_permission(self):
        for actor in ("inventario", "vendedor"):
            self.assertEqual(self.upload(actor=actor).status_code, 403)
            self.assertEqual(self.clients[actor].get("/reconciliation").status_code, 403)
        run = self.preview(); self.action(run)
        self.assertEqual(self.action(run, "resolve", {"note":"No", "action":"acknowledge"}, actor="inventario", line=True).status_code, 403)
        role = Role.query.filter_by(slug="admin").one()
        role.permissions.remove(Permission.query.filter_by(code="inventory:move").one())
        db.session.commit(); rbac_service.invalidate_cache()
        self.assertEqual(self.action(run, "resolve", {"note":"No", "action":"adjustment", "new_stock":13}, line=True).status_code, 403)
        self.assertEqual(self.action(run, "resolve", {"note":"OK", "action":"acknowledge"}, line=True).status_code, 200)

    def test_discard_unknown_product_and_ui(self):
        run = self.preview()
        self.assertEqual(self.action(run, "discard").status_code, 200)
        self.assertEqual(self.action(run).status_code, 409)
        unknown = self.upload(b"product_code;quantity\nUNKNOWN;3").json
        self.action(unknown)
        self.assertEqual(self.action(unknown, "resolve", {"note":"No", "action":"adjustment", "new_stock":3}, line=True).status_code, 409)
        self.assertEqual(self.clients["admin"].get("/reconciliation").status_code, 200)
        self.assertEqual(self.clients["admin"].get("/api/reconciliation").json["total"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
