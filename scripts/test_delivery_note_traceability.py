"""Delivery-note atomicity and presentation; disposable in-memory SQLite only."""
import unittest
from unittest.mock import patch

import dotenv

# Block the unconditional config loader before importing the application.
dotenv.load_dotenv = lambda *args, **kwargs: False

from approval_test_support import IsolatedCase
from flask_login import login_user
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import DeliveryNote, DeliveryNoteItem, Permission, Product, Role, StockMovement
from app.services import (dashboard_service, delivery_note_service as service,
                          inventory_service, movement_export_service, rbac_service)
from app.services.movement_traceability_service import movement_presentations


class DeliveryTraceabilityTests(IsolatedCase):
    def payload(self, multiple=False):
        items = [{"product_id": self.product.id, "quantity": 2}]
        if multiple:
            second = Product(code="TEST-02", name="Second synthetic product",
                             category_id=self.product.category_id, current_stock=20,
                             minimum_stock=0, purchase_price=2, sale_price=3,
                             purchase_price_currency="USD", sale_price_currency="USD", is_active=True)
            db.session.add(second)
            db.session.commit()
            items.append({"product_id": second.id, "quantity": 3})
        return {"customer_name": "Synthetic customer", "items": items}

    def issue(self, payload=None):
        result = self.clients["admin"].post("/api/delivery-notes", json=payload or self.payload())
        self.assertEqual(result.status_code, 201, result.json)
        return db.session.get(DeliveryNote, result.json["delivery_note"]["id"])

    def cancel(self, note_id):
        return self.clients["admin"].post(f"/api/delivery-notes/{note_id}/cancel", json={"reason": "Cancelación de prueba"})

    def snapshot(self):
        db.session.expire_all()
        return {model.__tablename__: [tuple(row) for row in db.session.execute(
            select(model.__table__).order_by(model.id)).all()]
            for model in (DeliveryNote, DeliveryNoteItem, Product, StockMovement)}

    def test_simple_and_multiple_issue_cancel(self):
        for multiple in (False, True):
            note = self.issue(self.payload(multiple))
            original = {i.product_id: i.product.current_stock + int(i.quantity) for i in note.items}
            for item in note.items:
                movement = item.issue_stock_movement
                self.assertIsNotNone(item.issue_stock_movement_id)
                self.assertIsNone(item.reversal_stock_movement_id)
                self.assertEqual((movement.product_id, movement.quantity, movement.movement_type),
                                 (item.product_id, int(item.quantity), "salida"))
            count = StockMovement.query.count()
            self.assertEqual(self.cancel(note.id).status_code, 200)
            db.session.expire_all()
            for item in note.items:
                movement = item.reversal_stock_movement
                self.assertEqual((movement.product_id, movement.quantity, movement.movement_type),
                                 (item.product_id, int(item.quantity), "entrada"))
                self.assertNotEqual(item.issue_stock_movement_id, item.reversal_stock_movement_id)
                self.assertEqual(item.product.current_stock, original[item.product_id])
            self.assertEqual(StockMovement.query.count(), count + len(note.items))
            before = self.snapshot()
            self.assertEqual(self.cancel(note.id).status_code, 409)
            self.assertEqual(self.snapshot(), before)

    def test_server_ids_rejected_and_invalid_issue_unchanged(self):
        for field in ("issue_stock_movement_id", "reversal_stock_movement_id"):
            for nested in (False, True):
                payload = self.payload()
                (payload["items"][0] if nested else payload)[field] = 1
                before = self.snapshot()
                self.assertEqual(self.clients["admin"].post("/api/delivery-notes", json=payload).status_code, 400)
                self.assertEqual(self.snapshot(), before)
        payload = self.payload()
        payload["items"][0]["quantity"] = 999
        self.assertEqual(self.clients["admin"].post("/api/delivery-notes", json=payload).status_code, 409)
        self.assertEqual(StockMovement.query.count(), 0)

    def test_issue_and_cancel_rollback_after_first_movement(self):
        payload = self.payload(True)
        original_movement = service.StockMovement
        for operation in ("issue", "cancel"):
            note_id = self.issue(payload).id if operation == "cancel" else None
            before = self.snapshot()
            calls = []

            def failing_movement(**kwargs):
                calls.append(kwargs)
                if len(calls) == 2:
                    db.session.flush()  # Force first item, stock and movement into the transaction.
                    raise RuntimeError("Synthetic intermediate failure")
                return original_movement(**kwargs)

            with self.app.test_request_context():
                login_user(self.users["admin"])
                with patch.object(service, "StockMovement", side_effect=failing_movement):
                    with self.assertRaisesRegex(RuntimeError, "Synthetic"):
                        service.cancel_note(note_id, "Cancelación de prueba") if note_id else service.create_note(payload)
            self.assertEqual(len(calls), 2)
            self.assertEqual(self.snapshot(), before)
            self.assertTrue(db.session.is_active)

    def test_flush_commit_and_fk_failures_rollback_and_recover(self):
        for failure in ("flush", "commit", "fk"):
            for operation in ("issue", "cancel"):
                payload = self.payload()
                note_id = self.issue(payload).id if operation == "cancel" else None
                before = self.snapshot()
                with self.app.test_request_context():
                    login_user(self.users["admin"])
                    if failure == "fk":
                        original = service.StockMovement

                        def invalid_movement(**kwargs):
                            kwargs["user_id"] = 2147483647
                            return original(**kwargs)

                        context = patch.object(service, "StockMovement", side_effect=invalid_movement)
                    else:
                        context = patch.object(db.session, failure, side_effect=RuntimeError("Synthetic persistence failure"))
                    with context:
                        with self.assertRaises((RuntimeError, IntegrityError), msg=f"{failure} {operation}"):
                            service.cancel_note(note_id, "Cancelación de prueba") if note_id else service.create_note(payload)
                self.assertEqual(self.snapshot(), before, (failure, operation))
                self.assertTrue(db.session.is_active)
                db.session.execute(select(Product.id)).all()

    def test_all_read_surfaces_and_exports(self):
        note = self.issue()
        self.assertEqual(self.cancel(note.id).status_code, 200)
        with self.app.test_request_context():
            login_user(self.users["admin"])
            rows = inventory_service.query_movements({})["items"]
            self.assertEqual(len(rows), 2)
            surfaces = [rows, inventory_service.movement_summary({})["recent"],
                        inventory_service.query_movements({"product_id": self.product.id})["items"],
                        dashboard_service.recent_movements()["items"]]
            for rows in surfaces:
                self.assertEqual(len(rows), 2)
                for row in rows:
                    self.assertEqual(row["source_type"], "delivery_note")
                    self.assertEqual(row["display_reference"], note.note_number)
                    self.assertEqual(row["source_url"], f"/delivery-notes?detail={note.id}")
                    self.assertEqual(row["movement_number"], f"MOV-{row['id']:06d}")
                    detail = inventory_service.get_movement(row["id"])
                    self.assertEqual(detail["display_reference"], note.note_number)
            exported, _, _ = movement_export_service.movement_rows({})
            self.assertEqual([r["reference"] for r in exported], [note.note_number] * 2)
            for format_name in ("csv", "xlsx"):
                output = movement_export_service.export_movements(format_name, {})
                self.assertTrue(output)
        for movement in StockMovement.query.all():
            response = self.clients["admin"].get(f"/api/inventory/movements/{movement.id}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["display_reference"], note.note_number)

    def test_permission_and_missing_detail(self):
        note = self.issue()
        role = Role.query.filter_by(slug="inventario").one()
        permission = Permission.query.filter_by(code="delivery_notes:read").one()
        role.permissions.remove(permission)
        db.session.commit()
        rbac_service.invalidate_cache()
        movement_id = note.items[0].issue_stock_movement_id
        response = self.clients["inventario"].get(f"/api/inventory/movements/{movement_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["display_reference"], note.note_number)
        self.assertIsNone(response.json["source_url"])
        self.assertEqual(self.clients["inventario"].get(f"/api/delivery-notes/{note.id}").status_code, 403)
        self.assertEqual(self.clients["inventario"].get(f"/delivery-notes?detail={note.id}").status_code, 403)
        self.assertEqual(self.clients["admin"].get("/api/delivery-notes/2147483647").status_code, 404)
        self.assertEqual(self.clients["admin"].get(f"/delivery-notes?detail={note.id}").status_code, 200)
        self.assertEqual(self.clients["admin"].get("/delivery-notes?detail=2147483647").status_code, 200)
        for detail in ("abc", "0", "-1", "1.5", "2147483648"):
            self.assertEqual(self.clients["admin"].get("/delivery-notes", query_string={"detail": detail}).status_code, 200, detail)
        self.assertEqual(self.clients["admin"].get("/delivery-notes?detail=1&detail=2").status_code, 200)

    def test_historical_null_and_fake_reason(self):
        note = self.issue()
        issue_id = note.items[0].issue_stock_movement_id
        note.items[0].issue_stock_movement = None  # Synthetic pre-migration fixture only.
        db.session.commit()
        before = db.session.execute(select(StockMovement.__table__)).all()
        movement = db.session.get(StockMovement, issue_id)
        self.assertIsNone(movement_presentations([movement])[issue_id]["source_type"])
        self.assertEqual(self.cancel(note.id).status_code, 200)
        db.session.expire_all()
        self.assertIsNone(note.items[0].issue_stock_movement_id)
        self.assertIsNotNone(note.items[0].reversal_stock_movement_id)
        self.assertEqual(db.session.execute(select(StockMovement.__table__).where(StockMovement.id == issue_id)).all(), before)
        fake = StockMovement(product_id=self.product.id, movement_type="salida", quantity=1,
                             previous_stock=10, new_stock=9, reason="Nota NE-999999")
        db.session.add(fake)
        db.session.commit()
        self.assertIsNone(movement_presentations([fake])[fake.id]["source_type"])

    def test_inconsistent_product_type_quantity_and_cross_phase(self):
        note = self.issue(self.payload(True))
        first, second = sorted(note.items, key=lambda item: item.id)
        movement = first.issue_stock_movement
        for field, value in (("product_id", second.product_id), ("quantity", 99), ("movement_type", "entrada")):
            previous = getattr(movement, field)
            setattr(movement, field, value)
            db.session.commit()
            shown = movement_presentations([movement])[movement.id]
            self.assertEqual(shown["source_type"], "inconsistent")
            self.assertEqual(shown["display_reference"], f"MOV-{movement.id:06d}")
            self.assertIsNone(shown["source_url"])
            setattr(movement, field, previous)
            db.session.commit()
        second.reversal_stock_movement = movement
        db.session.commit()
        self.assertEqual(movement_presentations([movement])[movement.id]["source_type"], "inconsistent")
        before = self.snapshot()
        self.assertEqual(self.cancel(note.id).status_code, 409)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
