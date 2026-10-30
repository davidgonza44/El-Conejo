"""Sales/cash and purchase separation on disposable SQLite only; no MySQL or dotenv.

SQLite proves atomic rollback and permissions, not InnoDB concurrent row locks.
"""
import unittest
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from approval_test_support import LinkedSupplierCase as IsolatedCase
from app.extensions import db
from app.models import Customer, Permission, Product, Role, Sale, SaleLine, StockMovement, User
from app.models.purchase import PurchaseOrder, PurchaseReception
from app.services import purchase_service, rbac_service, sale_service
from app.utils.permissions import ROLE_PERMISSIONS, role_has_permission


class SeparationTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        # Explicit legacy grants only in this disposable suite: keep testing cash,
        # ownership and atomic rollback without restoring production defaults.
        for slug, codes in {"vendedor": {"sales:read", "sales:write", "sales:create"},
                            "admin": {"sales:read", "sales:write"}}.items():
            role = Role.query.filter_by(slug=slug).one()
            role.permissions.extend(Permission.query.filter(Permission.code.in_(codes)).all())
        db.session.commit()
        rbac_service.invalidate_cache()
        self.product.sale_price = Decimal("27.00")
        self.customer = Customer(name="Cliente sintético")
        support = User(name="Soporte sintético", username="soporte", email="soporte@example.invalid", role="soporte_ti")
        db.session.add_all([self.customer, support])
        db.session.commit()
        self.clients["soporte_ti"] = self.client_for(support)

    def sale_body(self, **changes):
        return {"customer_id": self.customer.id, "payment_method": "cash", "payment_condition": "cash",
                "cash_received": "30.00", "idempotency_key": str(uuid4()),
                "items": [{"product_id": self.product.id, "quantity": 1}], **changes}

    def purchase_body(self):
        today = purchase_service.datetime.now(purchase_service.inventory_service.COMMERCIAL_ZONE).date().isoformat()
        quote = self.clients["asistente"].post("/api/supplier-quotes", json={
            "supplier_id": self.supplier.id, "issued_on": today, "currency": "VES", "valid_until": "2099-01-01",
            "items": [{"product_id": self.product.id, "quantity": 4, "unit_price": "2.00"}]})
        self.assertEqual(quote.status_code, 201, quote.json)
        return {"supplier_id": self.supplier.id, "supplier_quote_id": quote.json["id"],
                "items": [{"product_id": self.product.id, "quantity": 4}]}

    def snapshot(self):
        return (self.stock(), Sale.query.count(), SaleLine.query.count(), StockMovement.query.count(),
                PurchaseOrder.query.count(), PurchaseReception.query.count())

    def test_cash_above_equal_and_exact_historical_snapshots(self):
        for amount, change in (("30.00", "3.00"), ("27.00", "0.00")):
            response = self.clients["vendedor"].post("/api/sales", json=self.sale_body(cash_received=amount))
            self.assertEqual(response.status_code, 201, response.json)
            self.assertEqual((response.json["total"], response.json["cash_received"], response.json["change_due"]),
                             ("27.00", amount, change))
            saved = db.session.get(Sale, response.json["id"])
            self.assertIsInstance(saved.cash_received, Decimal)
            self.assertEqual(saved.change_due, Decimal(change))
        self.assertEqual(self.stock(), 8)
        self.assertEqual(StockMovement.query.count(), 2)
        # Explicit legacy grants only in this disposable suite: keep testing cash,
        # ownership and atomic rollback without restoring production defaults.
        for slug, codes in {"vendedor": {"sales:read", "sales:write", "sales:create"},
                            "admin": {"sales:read", "sales:write"}}.items():
            role = Role.query.filter_by(slug=slug).one()
            role.permissions.extend(Permission.query.filter(Permission.code.in_(codes)).all())
        db.session.commit()
        rbac_service.invalidate_cache()
        self.product.sale_price = Decimal("99.99")
        db.session.commit()
        detail = self.clients["admin"].get(f"/api/sales/{saved.sale_number}")
        self.assertEqual(detail.json["total"], "27.00")
        self.assertEqual(detail.json["cash_received"], "27.00")
        page = self.clients["admin"].get(f"/sales/{saved.sale_number}")
        self.assertIn("Efectivo recibido", page.text)
        self.assertIn("Vuelto", page.text)

    def test_insufficient_cash_rolls_back_sale_line_and_movement(self):
        before = self.snapshot()
        response = self.clients["vendedor"].post("/api/sales", json=self.sale_body(cash_received="20.00"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_cash_missing_invalid_or_oversized_rejected_without_writes(self):
        before = self.snapshot()
        for value in (None, "", "NaN", "Infinity", "-Infinity", "-1", "27.001", "1e100000", "10000000000", True, 30.0, [], {}):
            with self.subTest(value=value):
                result = self.clients["vendedor"].post("/api/sales", json=self.sale_body(cash_received=value))
                self.assertEqual(result.status_code, 400, result.json)
                self.assertEqual(self.snapshot(), before)
        payload = self.sale_body()
        payload.pop("cash_received")
        self.assertEqual(self.clients["vendedor"].post("/api/sales", json=payload).status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_non_cash_omitted_or_null_only(self):
        for method in ("transfer", "credit_card", "debit_card", "check", "receivable"):
            payload = self.sale_body(payment_method=method)
            payload.pop("cash_received")
            result = self.clients["vendedor"].post("/api/sales", json=payload)
            self.assertEqual(result.status_code, 201, result.json)
            self.assertIsNone(result.json["cash_received"])
            self.assertIsNone(result.json["change_due"])
        result = self.clients["vendedor"].post("/api/sales", json=self.sale_body(payment_method="transfer", cash_received=None))
        self.assertEqual(result.status_code, 201)
        before = self.snapshot()
        for value in ("30.00", "0", ""):
            result = self.clients["vendedor"].post("/api/sales", json=self.sale_body(payment_method="transfer", cash_received=value))
            self.assertEqual(result.status_code, 400)
            self.assertEqual(self.snapshot(), before)

    def test_unknown_top_level_and_item_fields_rejected(self):
        before = self.snapshot()
        for field in ("cash_recieved", "change_du", "change_due", "total", "unknown"):
            result = self.clients["vendedor"].post("/api/sales", json=self.sale_body(**{field: "3.00"}))
            self.assertEqual(result.status_code, 400, field)
        payload = self.sale_body(items=[{"product_id": self.product.id, "quantity": 1, "discount_precent": "5"}])
        self.assertEqual(self.clients["vendedor"].post("/api/sales", json=payload).status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_sale_retry_returns_original_cash_and_one_stock_exit(self):
        payload = self.sale_body()
        first = self.clients["vendedor"].post("/api/sales", json=payload)
        before = self.snapshot()
        retry = self.clients["vendedor"].post("/api/sales", json=payload)
        self.assertEqual((first.status_code, retry.status_code), (201, 200))
        self.assertEqual(first.json, retry.json)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.stock(), Sale.query.count(), StockMovement.query.count()), (9, 1, 1))

    def test_sales_authorization_matrix_valid_payloads_and_read_access(self):
        for actor in ("admin", "inventario", "soporte_ti"):
            self.assertEqual(self.clients[actor].post("/api/sales", json=self.sale_body()).status_code, 403, actor)
            self.assertEqual(self.clients[actor].get("/sales/new").status_code, 403, actor)
        self.assertEqual(self.clients["vendedor"].get("/sales/new").status_code, 200)
        result = self.clients["vendedor"].post("/api/sales", json=self.sale_body())
        self.assertEqual(result.status_code, 201)
        for path in ("/sales/list", f"/sales/{result.json['sale_number']}", f"/api/sales/{result.json['sale_number']}"):
            self.assertEqual(self.clients["admin"].get(path).status_code, 200, path)
        self.assertNotIn("Nueva venta</a>", self.clients["admin"].get("/sales/list").text)
        self.assertIn('data-can-sell="1"', self.clients["admin"].get("/sales/quotes").text)
        self.assertIn('data-can-sell="1"', self.clients["admin"].get("/sales/orders").text)

    def test_purchase_approval_receipt_and_complete_retry(self):
        result = self.clients["asistente"].post("/api/purchases", json=self.purchase_body())
        self.assertEqual(result.status_code, 201, result.json)
        order = result.json
        self.assertEqual(order["status"], "pending_approval")
        self.assertEqual((self.stock(), StockMovement.query.count()), (10, 0))
        path = f"/api/purchases/{order['id']}"
        receipt = {"received_at": purchase_service.datetime.now(purchase_service.inventory_service.COMMERCIAL_ZONE).date().isoformat(),
                   "items": [{"line_id": order["items"][0]["id"], "quantity": 4}]}
        for action in ("approve", "reject"):
            self.assertEqual(self.clients["inventario"].post(path + "/" + action, json={"reason": "No"} if action == "reject" else {}).status_code, 403)
        self.assertEqual(self.clients["inventario"].post(path + "/receptions", json=receipt).status_code, 409)
        self.assertEqual(self.clients["admin"].post("/api/purchases", json=self.purchase_body()).status_code, 403)
        self.assertEqual(self.clients["admin"].post(path + "/duplicate", json={}).status_code, 403)
        self.assertEqual(self.clients["admin"].post(path + "/approve", json={}).status_code, 200)
        self.assertEqual((self.stock(), StockMovement.query.count()), (10, 0))
        self.assertEqual(self.clients["admin"].post(path + "/receptions", json=receipt).status_code, 403)
        self.assertEqual(self.clients["inventario"].post(path + "/receptions", json=receipt).status_code, 200)
        self.assertEqual((self.stock(), StockMovement.query.count(), PurchaseReception.query.count()), (14, 1, 1))
        self.assertEqual(self.clients["inventario"].post(path + "/receptions", json=receipt).status_code, 409)
        self.assertEqual((self.stock(), StockMovement.query.count(), PurchaseReception.query.count()), (14, 1, 1))

    def test_legacy_pending_purchase_cannot_be_received(self):
        order = self.clients["asistente"].post("/api/purchases", json=self.purchase_body()).json
        # Historical fixture only, in disposable SQLite; ordinary creation stays pending_approval.
        db.session.get(PurchaseOrder, order["id"]).status = "pending"
        db.session.commit()
        before = self.snapshot()
        receipt = {"received_at": purchase_service.datetime.now(purchase_service.inventory_service.COMMERCIAL_ZONE).date().isoformat(),
                   "items": [{"line_id": order["items"][0]["id"], "quantity": 4}]}
        response = self.clients["inventario"].post(f"/api/purchases/{order['id']}/receptions", json=receipt)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_admin_rejects_inventory_purchase_without_stock(self):
        order = self.clients["asistente"].post("/api/purchases", json=self.purchase_body()).json
        result = self.clients["admin"].post(f"/api/purchases/{order['id']}/reject", json={"reason": "No prioritaria"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["status"], "rejected")
        self.assertEqual((self.stock(), StockMovement.query.count()), (10, 0))

    def test_replenishment_creation_uses_same_operational_authority(self):
        body = {"confirm": True, "items": [{"product_id": self.product.id, "quantity": 2}]}
        path = "/api/predictions/replenishment/orders"
        self.assertEqual(self.clients["admin"].post(path, json=body).status_code, 403)
        with patch("app.controllers.predictive_controller.validate_csrf"):
            result = self.clients["inventario"].post(path, json=body)
        self.assertEqual(result.status_code, 201, result.json)
        self.assertEqual(result.json["status"], "open")
        self.assertNotIn("unit_cost", result.json["items"][0])
        self.assertEqual((self.stock(), StockMovement.query.count()), (10, 0))

    def test_persisted_revocation_and_stale_cross_role_grants_cannot_bypass(self):
        for slug in ("admin", "inventario", "vendedor", "soporte_ti"):
            role = Role.query.filter_by(slug=slug).one()
            for permission in Permission.query.filter(Permission.code.in_(rbac_service.OPERATIONAL_ROLES)):
                if permission not in role.permissions:
                    role.permissions.append(permission)
        custom = Role(slug="custom", name="Custom", is_system=False, is_active=True)
        custom.permissions = Permission.query.all()
        user = User(name="Custom", username="custom", email="custom@example.invalid", role="custom")
        db.session.add_all([custom, user])
        db.session.commit()
        self.clients["custom"] = self.client_for(user)
        for actor in ("admin", "inventario", "soporte_ti", "custom"):
            self.assertEqual(self.clients[actor].post("/api/sales", json=self.sale_body()).status_code, 403)
        for actor in ("admin", "vendedor", "soporte_ti", "custom"):
            self.assertEqual(self.clients[actor].post("/api/purchases", json=self.purchase_body()).status_code, 403)
        order = self.clients["asistente"].post("/api/purchases", json=self.purchase_body()).json
        for actor in ("inventario", "vendedor", "soporte_ti", "custom"):
            self.assertEqual(self.clients[actor].post(f"/api/purchases/{order['id']}/approve", json={}).status_code, 403)
        seller = Role.query.filter_by(slug="vendedor").one()
        seller.permissions.remove(Permission.query.filter_by(code="sales:create").one())
        db.session.commit()
        self.assertEqual(self.clients["vendedor"].post("/api/sales", json=self.sale_body()).status_code, 403)

    def test_direct_services_enforce_roles_and_missing_database_role_denies(self):
        from flask_login import login_user
        from app.services.exceptions import ValidationError

        purchase = self.purchase_body()
        with self.app.test_request_context():
            login_user(self.users["admin"])
            for fn, args in ((sale_service.create_sale, (self.sale_body(),)),
                             (purchase_service.create_order, (purchase,)),
                             (purchase_service.receive_order, (123, {}))):
                with self.assertRaises(ValidationError) as error:
                    fn(*args)
                self.assertEqual(error.exception.status_code, 403)
        db.session.delete(Role.query.filter_by(slug="vendedor").one())
        db.session.commit()
        rbac_service.invalidate_cache()
        self.assertEqual(rbac_service.permissions_for("vendedor"), frozenset())
        with patch.object(rbac_service, "is_available", return_value=False):
            self.assertFalse(role_has_permission("vendedor", "sales:create"))
            self.assertFalse(role_has_permission("admin", "sales:create"))
            self.assertTrue(role_has_permission("inventario", "purchases:receive"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
