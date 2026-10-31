"""Integración de Inventario, Compras y Notas sobre la línea 5011 (SQLite desechable).

Cubre lo que faltaba al consolidar: clasificación persistente de servicios, precio
confirmado obligatorio en Notas, validaciones de Notas y el mensaje de 409 según la
causa real de la violación de integridad.
"""
import unittest

from approval_test_support import IsolatedCase
from flask_login import login_user
from test_purchase_tutor_20261005 import TutorFlowCase
from sqlalchemy import text

from app.extensions import db
from app.models import DeliveryNote, Product, StockMovement
from app.models.replenishment_request import ReplenishmentRequest
from app.models.supplier_quote import SupplierQuote
from app.services import inventory_service


class ServiceClassification(TutorFlowCase):
    def mark_service(self):
        self.product.current_stock = 0
        self.product.is_service = True
        db.session.commit()
        db.session.expire_all()
        self.assertTrue(db.session.get(Product, self.product.id).is_service)  # persistido, no en memoria

    def test_service_request_is_rejected_at_creation_without_records(self):
        self.mark_service()
        before = (ReplenishmentRequest.query.count(), SupplierQuote.query.count())
        request = self.clients["inventario"].post("/api/replenishment-requests",
                                                  json={"items": [{"product_id": self.product.id, "quantity": 2}]})
        self.assertEqual(request.status_code, 400, request.json)
        self.assertIn("servicio", request.json["error"].lower())
        quote = self.clients["asistente"].post("/api/supplier-quotes", json={
            "supplier_id": self.supplier.id, "currency": "VES", "issued_on": self.today, "valid_until": "2099-01-01",
            "items": [{"product_id": self.product.id, "quantity": 2, "unit_price": "2.00"}]})
        self.assertEqual(quote.status_code, 400, quote.json)
        self.assertEqual((ReplenishmentRequest.query.count(), SupplierQuote.query.count()), before)

    def test_service_cannot_receive_stock_entries(self):
        self.mark_service()
        response = self.clients["inventario"].post("/api/inventory/entry",
                                                   json={"product_id": self.product.id, "quantity": 1})
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(db.session.get(Product, self.product.id).current_stock, 0)


class ApprovalsInbox(TutorFlowCase):
    def test_inbox_keeps_the_shell_layout_and_readable_references(self):
        body = dict(name="Proveedor Renombrado", tax_id="TEST-SUPPLIER", contact_name="Contacto", phone="0000000000",
                    email="test@example.invalid", category="Pruebas", is_active=True)
        self.assertEqual(self.clients["asistente"].patch(f"/api/suppliers/{self.supplier.id}", json=body).status_code, 200)
        page = self.clients["admin"].get("/purchases/approvals")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn('class="ix-main po-page', html)  # sin el espacio la página quedaba bajo el menú lateral
        self.assertNotIn("ix-mainpo-page", html)
        self.assertIn(self.supplier.name, html)  # referencia legible, no el id interno
        self.assertIn("Pendiente de decisión", html)
        self.assertIn("Revisar", html)
        empty = self.clients["admin"].get("/purchases/approvals?page=1")
        self.assertEqual(empty.status_code, 200)


class ServiceProposal(IsolatedCase):
    def body(self, code, **extra):
        return {"code": code, "name": "Servicio " + code, "category_id": self.product.category_id, "unit": "servicio",
                "minimum_stock": 0, "purchase_price": 1, "sale_price": 5,
                "purchase_price_currency": "USD", "sale_price_currency": "USD", **extra}

    def test_classification_is_validated_and_approved_by_admin(self):
        self.assertEqual(self.clients["asistente"].post("/api/products", json=self.body("SRV-1", is_service="si")).status_code, 400)
        self.assertEqual(self.clients["asistente"].post(
            "/api/products", json=self.body("SRV-2", is_service=True, current_stock=3)).status_code, 400)
        created = self.clients["asistente"].post("/api/products", json=self.body("SRV-3", is_service=True))
        self.assertEqual(created.status_code, 201, created.json)
        self.assertEqual(self.clients["admin"].post(f"/api/products/{created.json['id']}/approve", json={}).status_code, 200)
        self.assertTrue(db.session.get(Product, created.json["id"]).is_service)
        # Un producto con existencias no se reclasifica como servicio.
        self.assertEqual(self.clients["asistente"].patch(f"/api/products/{self.product.id}",
                                                         json={"is_service": True}).status_code, 409)


class DeliveryNoteConfirmedPrice(IsolatedCase):
    def post(self, *items, **extra):
        return self.clients["vendedor"].post("/api/delivery-notes", json={"customer_name": "Cliente", "items": list(items), **extra})

    def item(self, **extra):
        return {"product_id": self.product.id, "quantity": 1, **extra}

    def test_confirmed_price_is_mandatory_and_strict(self):
        for item in (self.item(), self.item(expected_unit_price="3.001"), self.item(expected_unit_price="-1"),
                     self.item(expected_unit_price=True), self.item(expected_unit_price="abc")):
            response = self.post(item)
            self.assertEqual(response.status_code, 400, (item, response.json))
        stale = self.post(self.item(expected_unit_price="2.99"))
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json["code"], "price_changed")
        self.assertEqual((self.stock(), StockMovement.query.count(), DeliveryNote.query.count()), (10, 0, 0))
        legacy = self.post(self.item(unit_price="3.00"))  # nombre anterior, mismo control
        self.assertEqual(legacy.status_code, 201, legacy.json)
        self.assertEqual(self.post(self.item(expected_unit_price="3.00")).status_code, 201)
        self.assertEqual(self.stock(), 8)

    def test_customer_fields_ids_and_total_are_validated(self):
        price = {"expected_unit_price": "3.00"}
        for extra in ({"customer_name": "Cliente\x07"}, {"customer_address": "Calle\x00 1"}):
            self.assertEqual(self.post(self.item(**price), **extra).status_code, 400, extra)
        self.assertEqual(self.post({"product_id": -1, "quantity": 1, **price}).status_code, 400)
        self.product.sale_price = 9_999_999_999
        self.product.current_stock = 10
        db.session.commit()
        response = self.post({"product_id": self.product.id, "quantity": 2, "expected_unit_price": "9999999999.00"})
        self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual((self.stock(), DeliveryNote.query.count()), (10, 0))


class IntegrityMessages(IsolatedCase):
    def test_409_names_the_real_cause(self):
        @self.app.route("/__probe/<kind>", methods=["POST"])
        def probe(kind):
            statements = {
                "check": "UPDATE products SET current_stock = -1",
                "unique": "INSERT INTO products (code, name, category_id, unit, current_stock, minimum_stock, purchase_price, "
                          "sale_price, is_active, approval_status, is_service, created_at, updated_at) "
                          "SELECT code, name, category_id, unit, 0, 0, 0, 0, 1, 'approved', 0, created_at, updated_at FROM products",
            }
            db.session.execute(text(statements[kind]))
            db.session.commit()
            return "", 204

        check = self.clients["admin"].post("/__probe/check", json={})
        unique = self.clients["admin"].post("/__probe/unique", json={})
        self.assertEqual((check.status_code, unique.status_code), (409, 409))
        self.assertIn("datos inválidos", check.json["error"])
        self.assertNotIn("mismos datos", check.json["error"])
        self.assertIn("mismos datos", unique.json["error"])
        self.assertEqual(self.stock(), 10)

    def test_mariadb_check_violation_is_409_not_500(self):
        # MariaDB informa CHECK (4025) como OperationalError; no debe terminar en 500.
        from unittest.mock import patch
        from sqlalchemy.exc import OperationalError
        failure = OperationalError("UPDATE products ...", {}, Exception(4025, "CONSTRAINT `ck_products_stock` failed"))
        with patch.object(inventory_service, "lock_products", side_effect=failure):
            response = self.clients["vendedor"].post("/api/delivery-notes", json={"customer_name": "Cliente",
                "items": [{"product_id": self.product.id, "quantity": 1, "expected_unit_price": "3.00"}]})
        self.assertEqual(response.status_code, 409, response.json)
        self.assertIn("datos inválidos", response.json["error"])
        self.assertEqual(self.stock(), 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
