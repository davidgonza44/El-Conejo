"""Regresión de propuestas de producto en SQLite desechable, sin MySQL ni archivos de usuario."""
import unittest
from datetime import datetime
from unittest.mock import patch

from approval_test_support import IsolatedCase
from app.extensions import db
from app.models import Category, Product, ProductChange, ProductStateHistory, StockMovement
from app.models.role import Permission, Role
from app.services import product_change_service, product_image_service, product_state_history_service, rbac_service
from app.services.exceptions import ValidationError
from flask_login import login_user


FIELDS = ("code", "name", "category_id", "unit", "description", "brand", "purchase_price", "sale_price",
          "purchase_price_currency", "sale_price_currency",
          "minimum_stock", "is_active", "image_url", "current_stock", "updated_at", "approval_status")


class ProductChangesTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        product_state_history_service.record_baseline(self.product, datetime.utcnow())
        db.session.commit()
        self.path = f"/api/products/{self.product.id}"

    def snapshot(self):
        db.session.expire_all()
        product = db.session.get(Product, self.product.id)
        return {field: getattr(product, field) for field in FIELDS}

    def propose(self, changes, method="put"):
        for price in ("sale_price", "purchase_price"):
            if price in changes:
                changes.setdefault(price + "_currency", "USD")  # This fixture deliberately uses USD.
        response = getattr(self.clients["asistente"], method)(self.path, json=changes)
        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()
        self.assertEqual(result["pending_change"]["status"], "pending")
        return result["pending_change"]

    def decide(self, proposal, approve=True, client="admin"):
        body = {"proposal_id": proposal["id"]}
        if not approve:
            body["reason"] = "Datos no autorizados"
        return self.clients[client].post(self.path + ("/approve" if approve else "/reject"), json=body)

    def test_admin_inventory_and_incompatible_grants_cannot_write(self):
        original = self.snapshot()
        for role in ("admin", "inventario", "vendedor"):
            persisted = Role.query.filter_by(slug=role).one()
            persisted.permissions.append(Permission.query.filter_by(code="products:write").one())
            db.session.commit()
            rbac_service.invalidate_cache()
            for method in ("put", "patch", "delete"):
                response = getattr(self.clients[role], method)(self.path, json={"name": "Intento directo"})
                self.assertEqual(response.status_code, 403, response.get_json())
            for method in ("post", "delete"):
                self.assertEqual(getattr(self.clients[role], method)(self.path + "/image").status_code, 403)
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(ProductChange.query.count(), 0)

    def test_assistant_proposes_all_fields_and_admin_applies_exactly(self):
        second = Category(name="Segunda categoría")
        db.session.add(second)
        self.product.current_stock = 0  # Synthetic fixture: only a product without stock can be deactivated.
        db.session.commit()
        original = self.snapshot()
        changes = {"name": "Nombre nuevo", "category_id": second.id, "unit": "caja", "description": "Descripción nueva",
                   "brand": "Marca nueva", "purchase_price": "5.25", "sale_price": "9.75",
                   "minimum_stock": 7, "is_active": False}
        proposal = self.propose(changes)
        self.assertEqual(proposal["changes"], changes)
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(StockMovement.query.count(), 0)
        self.assertEqual(ProductStateHistory.query.count(), 1)
        self.assertEqual(self.decide(proposal, client="asistente").status_code, 403)
        self.assertEqual(self.decide(proposal, client="inventario").status_code, 403)
        response = self.decide(proposal)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertIsNone(response.get_json()["pending_change"])
        actual = self.snapshot()
        for field in FIELDS:
            if field == "updated_at":
                continue
            expected = changes.get(field, original[field])
            self.assertEqual(str(actual[field]), str(expected), field)
        history = ProductStateHistory.query.order_by(ProductStateHistory.id.desc()).first()
        self.assertEqual((history.previous_minimum_stock, history.new_minimum_stock), (0, 7))
        self.assertEqual((history.previous_is_active, history.new_is_active), (True, False))
        self.assertEqual(history.changed_by_user_id, self.users["admin"].id)
        self.assertEqual(StockMovement.query.count(), 0)
        self.assertEqual(self.decide(proposal).status_code, 409)
        self.assertEqual(self.decide(proposal, False).status_code, 409)

    def test_rejection_keeps_every_previous_value_and_records_reason(self):
        original = self.snapshot()
        proposal = self.propose({"name": "Rechazado", "minimum_stock": 99, "sale_price": "15.00", "brand": "Otra"}, "patch")
        self.assertEqual(self.clients["admin"].post(self.path + "/reject", json={"proposal_id": proposal["id"]}).status_code, 400)
        response = self.decide(proposal, False)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(response.get_json()["last_change"]["rejection_reason"], "Datos no autorizados")
        self.assertEqual(response.get_json()["last_change"]["status"], "rejected")
        self.assertEqual(ProductStateHistory.query.count(), 1)
        self.assertEqual(StockMovement.query.count(), 0)
        self.propose({"name": "Otra propuesta"})

    def test_proposer_cannot_decide_after_becoming_admin(self):
        proposal = self.propose({"name": "Autoaprobación"})
        self.users["asistente"].role = "admin"
        db.session.commit()
        for approve in (True, False):
            self.assertEqual(self.decide(proposal, approve, "asistente").status_code, 403)
        self.assertEqual(ProductChange.query.one().status, "pending")

    def test_pending_proposal_is_immutable_and_decision_requires_its_id(self):
        proposal = self.propose({"name": "Primera propuesta"})
        self.assertEqual(self.clients["asistente"].put(self.path, json={"name": "Segunda"}).status_code, 409)
        self.assertEqual(self.clients["admin"].post(self.path + "/approve", json={}).status_code, 409)
        self.assertEqual(self.clients["admin"].post(self.path + "/approve", json={"proposal_id": "1"}).status_code, 400)
        self.assertEqual(ProductChange.query.one().changes, proposal["changes"])

    def test_no_partial_application_when_category_or_code_becomes_invalid(self):
        category = Category(name="Categoría a desactivar")
        db.session.add(category)
        db.session.commit()
        original = self.snapshot()
        proposal = self.propose({"name": "No aplicar", "category_id": category.id})
        category.is_active = False
        db.session.commit()
        self.assertEqual(self.decide(proposal).status_code, 400)
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(ProductChange.query.one().status, "pending")

    def test_stock_and_internal_fields_are_rejected_without_a_proposal(self):
        for changes in ({"current_stock": 999}, {"approval_status": "approved"}, {"proposed_by_user_id": 1},
                        {"minimum_stock": 1.5}, {"is_active": "false"}, {"sale_price": "NaN"},
                        {"sale_price": "Infinity"}, {"sale_price": "1.234"}, {"brand": 2}, {"name": ""}):
            self.assertEqual(self.clients["asistente"].put(self.path, json=changes).status_code, 400, changes)
        self.assertEqual(ProductChange.query.count(), 0)

    def test_deactivation_is_a_proposal_and_reactivation_requires_approval(self):
        self.assertEqual(self.clients["asistente"].delete(self.path).status_code, 409)  # 10 units in stock
        self.product.current_stock = 0  # Synthetic fixture: stock already taken to zero.
        db.session.commit()
        response = self.clients["asistente"].delete(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.snapshot()["is_active"])
        self.assertEqual(self.decide(response.get_json()["product"]["pending_change"]).status_code, 200)
        self.assertFalse(self.snapshot()["is_active"])
        proposal = self.propose({"is_active": True})
        self.assertFalse(self.snapshot()["is_active"])
        self.assertEqual(self.decide(proposal).status_code, 200)
        self.assertTrue(self.snapshot()["is_active"])

    def test_image_replacement_and_data_are_one_proposal_without_deleting_current_file(self):
        self.product.image_url = "/media/products/current.png"
        db.session.commit()
        original = self.snapshot()
        with self.app.test_request_context():
            login_user(self.users["asistente"])
            with patch("app.services.upload_service.save_image", return_value="proposed.png"), \
                    patch("app.services.upload_service.delete_image") as delete:
                product_image_service.set_image(self.product.id, None, {"name": "Con imagen"})
                delete.assert_not_called()
        self.assertEqual(self.snapshot(), original)
        proposal = ProductChange.query.one().to_dict()
        self.assertEqual(proposal["changes"], {"name": "Con imagen", "image_url": "/media/products/proposed.png"})
        self.assertEqual(self.decide(proposal).status_code, 200)
        self.assertEqual(self.snapshot()["image_url"], "/media/products/proposed.png")

    def test_image_removal_needs_approval_and_rejection_keeps_image(self):
        self.product.image_url = "/media/products/current.png"
        db.session.commit()
        result = self.clients["asistente"].delete(self.path + "/image")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.snapshot()["image_url"], "/media/products/current.png")
        proposal = result.get_json()["product"]["pending_change"]
        self.assertEqual(proposal["changes"], {"image_url": None})
        self.assertEqual(self.decide(proposal, False).status_code, 200)
        self.assertEqual(self.snapshot()["image_url"], "/media/products/current.png")

    def test_cost_and_price_are_hidden_in_proposals_from_inventory(self):
        self.propose({"purchase_price": "5.00", "sale_price": "8.00", "name": "Visible"})
        detail = self.clients["inventario"].get(self.path).get_json()
        for key in ("pending_change", "last_change"):
            self.assertNotIn("purchase_price", detail[key]["changes"])
            self.assertNotIn("sale_price", detail[key]["previous_values"])
        listing = self.clients["admin"].get("/api/products").get_json()
        self.assertTrue(listing["items"][0]["has_pending_change"])

    def test_admin_page_has_no_initial_edit_action(self):
        html = self.clients["admin"].get("/products").get_data(as_text=True)
        self.assertIn('window.CAN_WRITE = false', html)
        self.assertIn('id="pl-action" class="pl-primary" hidden', html)
        self.assertNotIn('>Editar producto<', html)
        self.assertIn('window.CAN_APPROVE_PRODUCTS = true', html)

    def test_service_itself_denies_admin_proposals(self):
        with self.app.test_request_context():
            login_user(self.users["admin"])
            with self.assertRaises(ValidationError) as error:
                product_change_service.propose_change(self.product.id, {"name": "Directo"})
            self.assertEqual(error.exception.status_code, 403)

    def test_brand_null_preserves_and_empty_clears_only_after_approval(self):
        self.product.brand = "Bosch"
        db.session.commit()
        proposal = self.propose({"brand": None, "name": "Conserva marca"})
        self.assertNotIn("brand", proposal["changes"])
        self.assertEqual(self.decide(proposal).status_code, 200)
        self.assertEqual(self.snapshot()["brand"], "Bosch")
        proposal = self.propose({"brand": ""})
        self.assertEqual(self.snapshot()["brand"], "Bosch")
        self.assertEqual(self.decide(proposal).status_code, 200)
        self.assertIsNone(self.snapshot()["brand"])

    def test_code_collision_at_approval_does_not_apply_other_fields(self):
        proposal = self.propose({"code": "TAKEN", "name": "No aplicar"})
        original = self.snapshot()
        db.session.add(Product(code="TAKEN", name="Otro producto", category_id=self.product.category_id))
        db.session.commit()
        self.assertEqual(self.decide(proposal).status_code, 409)
        self.assertEqual(self.snapshot(), original)

    def test_stale_proposal_does_not_overwrite_a_changed_master_value(self):
        proposal = self.propose({"name": "Propuesto"})
        self.product.name = "Cambio externo"
        db.session.commit()
        self.assertEqual(self.decide(proposal).status_code, 409)
        self.assertEqual(self.snapshot()["name"], "Cambio externo")
        self.assertEqual(ProductChange.query.one().status, "pending")
        self.assertEqual(self.decide(proposal, False).status_code, 200)

    def test_assistant_page_explains_proposal_and_has_review_disabled(self):
        html = self.clients["asistente"].get("/products").get_data(as_text=True)
        self.assertIn('window.CAN_WRITE = true', html)
        self.assertIn('window.CAN_APPROVE_PRODUCTS = false', html)
        self.assertIn('Los cambios se enviarán al Administrador.', html)


if __name__ == "__main__":
    unittest.main()
