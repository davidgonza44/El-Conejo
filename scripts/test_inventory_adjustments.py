"""Manual workflow on disposable SQLite; no operational DB or dotenv."""
import importlib.util
import unittest
from unittest.mock import patch
from pathlib import Path

from approval_test_support import IsolatedCase
from app.extensions import db
from app.models import InventoryAdjustmentRequest, Permission, Role, StockMovement, User
from app.services import inventory_adjustment_service, rbac_service
from app.services.exceptions import ValidationError
from flask_login import login_user


class AdjustmentTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        support = User(name="Soporte", username="soporte", email="soporte@example.invalid", role="soporte_ti")
        db.session.add(support)
        db.session.commit()
        self.clients["soporte_ti"] = self.client_for(support)

    def create(self, stock=12):
        return self.clients["inventario"].post("/api/inventory/adjustment", json={
            "product_id": self.product.id, "new_stock": stock, "reason": "Conteo sintético"})

    def review(self, row, action="approve", actor="admin"):
        return self.clients[actor].post(f"/api/inventory/adjustments/{row['id']}/{action}",
                                       json={"review_comment": "Revisado"})

    def test_pending_approve_once_and_terminal_states(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.json)
        row = response.json
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["stock_movement_id"])
        self.assertEqual((self.stock(), StockMovement.query.count()), (10, 0))
        self.assertEqual(self.review(row, actor="inventario").status_code, 403)
        approved = self.review(row)
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertEqual(approved.json["reviewer_user_id"], self.users["admin"].id)
        self.assertEqual(approved.json["status"], "approved")
        self.assertEqual((self.stock(), StockMovement.query.count()), (12, 1))
        movement = db.session.get(StockMovement, approved.json["stock_movement_id"])
        self.assertEqual((movement.previous_stock, movement.new_stock, movement.movement_type), (10, 12, "ajuste"))
        for action in ("approve", "reject"):
            self.assertEqual(self.review(row, action).status_code, 409)
        self.assertEqual((self.stock(), StockMovement.query.count()), (12, 1))

    def test_reject_and_rejected_cannot_approve(self):
        row = self.create().json
        result = self.review(row, "reject")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["status"], "rejected")
        self.assertIsNone(result.json["stock_movement_id"])
        self.assertEqual(self.review(row).status_code, 409)
        self.assertEqual((self.stock(), StockMovement.query.count()), (10, 0))

    def test_authorization_scope_and_forged_fields(self):
        body = {"product_id": self.product.id, "new_stock": 12, "reason": "Test"}
        row = self.create().json
        for actor in ("admin", "vendedor", "soporte_ti"):
            self.assertEqual(self.clients[actor].post("/api/inventory/adjustment", json=body).status_code, 403)
        for actor in ("vendedor", "soporte_ti", "inventario"):
            self.assertEqual(self.review(row, actor=actor).status_code, 403)
        for actor in ("vendedor", "soporte_ti"):
            self.assertEqual(self.clients[actor].get("/api/inventory/adjustments").status_code, 403)
        for field in ("status", "requester_user_id", "reviewer_user_id", "stock_movement_id", "previous_stock"):
            self.assertEqual(self.clients["inventario"].post("/api/inventory/adjustment", json={**body, field: 1}).status_code, 400)
        other = User(name="Otro", username="otro", email="otro@example.invalid", role="inventario")
        db.session.add(other); db.session.commit()
        client = self.client_for(other)
        self.assertEqual(client.get("/api/inventory/adjustments").json["total"], 0)
        self.assertEqual(client.get(f"/api/inventory/adjustments/{row['id']}").status_code, 404)

    def test_role_change_does_not_allow_self_approval(self):
        row = self.create().json
        self.users["inventario"].role = "admin"
        db.session.commit()
        self.assertEqual(self.review(row, actor="inventario").status_code, 403)
        self.assertEqual((self.stock(), StockMovement.query.count()), (10, 0))

    def test_stale_stock_and_round_trip_movement_conflict(self):
        row = self.create().json
        body = {"product_id": self.product.id, "quantity": 1, "reason": "Movimiento posterior"}
        self.assertEqual(self.clients["inventario"].post("/api/inventory/entry", json=body).status_code, 201)
        self.assertEqual(self.review(row).status_code, 409)
        self.assertEqual(self.clients["inventario"].post("/api/inventory/exit", json=body).status_code, 201)
        self.assertEqual(self.stock(), 10)
        self.assertEqual(self.review(row).status_code, 409)
        self.assertEqual(StockMovement.query.filter_by(movement_type="ajuste").count(), 0)
        self.assertEqual(db.session.get(InventoryAdjustmentRequest, row["id"]).status, "pending")

    def test_direct_service_and_stale_grants(self):
        for role in Role.query.all():
            for code in ("inventory_adjustments:create", "inventory_adjustments:approve"):
                permission = Permission.query.filter_by(code=code).one()
                if permission not in role.permissions: role.permissions.append(permission)
        db.session.commit()
        row = self.create().json
        self.assertEqual(self.review(row, actor="inventario").status_code, 403)
        with self.app.test_request_context():
            login_user(self.users["vendedor"])
            with self.assertRaises(ValidationError) as raised:
                inventory_adjustment_service.create_request({})
            self.assertEqual(raised.exception.status_code, 403)

    def test_commit_failure_rolls_back_request_stock_and_movement(self):
        row = self.create().json
        with self.app.test_request_context():
            login_user(self.users["admin"])
            with patch.object(db.session, "commit", side_effect=RuntimeError("Synthetic commit failure")):
                with self.assertRaises(RuntimeError):
                    inventory_adjustment_service.review_request(row["id"], {}, approve=True)
        self.assertEqual((self.stock(), StockMovement.query.count()), (10, 0))
        pending = db.session.get(InventoryAdjustmentRequest, row["id"])
        self.assertEqual(pending.status, "pending")
        self.assertIsNone(pending.stock_movement_id)
        self.assertIsNone(pending.reviewer_user_id)

    def test_migration_is_additive_idempotent_and_preserves_requests(self):
        row = self.create().json
        spec = importlib.util.spec_from_file_location("adjustment_migration", Path(__file__).resolve().parents[1] / "migrations/versions/0004_inventory_adjustments.py")
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        with db.engine.begin() as connection:
            migration.apply_changes(connection)
            migration.apply_changes(connection)
        self.assertEqual(InventoryAdjustmentRequest.query.count(), 1)
        self.assertEqual(db.session.get(InventoryAdjustmentRequest, row["id"]).status, "pending")
        self.assertEqual((self.stock(), StockMovement.query.count()), (10, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
