"""DEMO cierra venta, listado, cotización y pedido de cliente en el servidor.

El mapa y los seeds tampoco conceden ventas fuera de la DEMO.
SQLite en memoria. No abre MySQL ni el puerto 5000.
"""
import unittest
from decimal import Decimal
from uuid import uuid4

from approval_test_support import LinkedSupplierCase as IsolatedCase
from app.extensions import db
from app.models import Customer, Permission, Quote, Role, Sale, User
from app.models.order import Order
from app.services import rbac_service
from app.services.exceptions import ValidationError
from app.utils.permissions import WITHDRAWN_CUSTOMER_SALES

_ROLES = ("admin", "inventario", "asistente", "vendedor", "soporte_ti")
_PAGES = ("/sales/new", "/sales/list", "/sales/orders", "/sales/quotes")
_READS = (
    "/sales", "/sales/VTA-000001", "/sales/orders/PED-000001/print",
    "/api/sales", "/api/sales/dashboard", "/api/sales/options", "/api/sales/VTA-000001",
    "/api/quotes", "/api/quotes/options", "/api/quotes/COT-000001",
    "/api/orders", "/api/orders/options", "/api/orders/PED-000001",
)
_WRITES = (
    ("post", "/api/sales"),
    ("post", "/api/quotes"),
    ("post", "/api/orders"),
    ("post", "/api/orders/PED-000001/duplicate"),
    ("patch", "/api/orders/PED-000001"),
    ("patch", "/api/orders/PED-000001/status"),
    ("patch", "/api/orders/PED-000001/payment-status"),
    ("patch", "/api/quotes/COT-000001/status"),
    ("put", "/api/sales"),
    ("put", "/api/quotes"),
    ("put", "/api/orders"),
    ("put", "/api/orders/PED-000001"),
    ("put", "/api/quotes/COT-000001"),
)


class WithdrawnCustomerSalesTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        support = User(name="Soporte sintético", username="soporte", email="soporte@example.invalid", role="soporte_ti")
        self.customer = Customer(name="Cliente sintético")
        self.product.sale_price = Decimal("10.00")
        db.session.add_all([support, self.customer])
        db.session.commit()
        self.users["soporte_ti"] = support
        self.clients["soporte_ti"] = self.client_for(support)

    def assert_default_sales_denied(self):
        for role in _ROLES:
            self.assertTrue(WITHDRAWN_CUSTOMER_SALES.isdisjoint(rbac_service.permissions_for(role)), role)
            client = self.clients[role]
            for path in (*_PAGES, *_READS):
                self.assertEqual(client.get(path).status_code, 403, f"{role} GET {path}")
            for method, path in _WRITES:
                if method != "put":  # Unsupported verbs are 405 outside the DEMO guard.
                    self.assertEqual(getattr(client, method)(path, json={}).status_code, 403,
                                     f"{role} {method} {path}")
        anonymous = self.app.test_client()
        for path in (*_PAGES, *(p for p in _READS if not p.startswith("/api/"))):
            response = anonymous.get(path)
            self.assertEqual(response.status_code, 302, path)
            self.assertIn("/login", response.headers["Location"])
        for path in (p for p in _READS if p.startswith("/api/")):
            self.assertEqual(anonymous.get(path).status_code, 401, path)
        for method, path in _WRITES:
            if method != "put":
                self.assertEqual(getattr(anonymous, method)(path, json={}).status_code, 401, path)
        self.assertEqual(self.clients["vendedor"].get("/products").status_code, 200)

    def test_new_seed_and_repeated_seed_never_grant_sales(self):
        from app.utils.permissions import ROLE_PERMISSIONS
        from migrate_operational_separation import GRANTS as operational_grants
        from migrate_tutor_cycle import GRANTS as tutor_grants
        self.assertFalse(rbac_service.customer_sales_withdrawn())
        for mapping in (ROLE_PERMISSIONS, operational_grants, tutor_grants):
            for codes in mapping.values():
                self.assertTrue(WITHDRAWN_CUSTOMER_SALES.isdisjoint(codes))
        rbac_service.ensure_seeded()
        for role in Role.query.all():
            self.assertTrue(WITHDRAWN_CUSTOMER_SALES.isdisjoint(role.permission_codes), role.slug)
        self.assertEqual(Permission.query.filter(Permission.code.in_(WITHDRAWN_CUSTOMER_SALES)).count(), 3)
        self.assert_default_sales_denied()

    def test_missing_rbac_tables_uses_safe_static_fallback(self):
        from sqlalchemy import inspect
        from app.utils.schema import reset_cache
        db.session.remove()
        for name in ("role_permissions", "roles", "permissions"):
            db.metadata.tables[name].drop(db.engine)
        reset_cache()
        rbac_service.invalidate_cache()
        self.assertTrue({"roles", "permissions", "role_permissions"}.isdisjoint(inspect(db.engine).get_table_names()))
        self.assertFalse(rbac_service.is_available())
        rbac_service.ensure_seeded()
        self.assert_default_sales_denied()

    def test_demo_blocks_pages_and_apis_and_keeps_history(self):
        # Simulate an old installation with legacy grants and synthetic history.
        seller = Role.query.filter_by(slug="vendedor").one()
        seller.permissions.extend(Permission.query.filter(Permission.code.in_(WITHDRAWN_CUSTOMER_SALES)).all())
        inventory = Role.query.filter_by(slug="inventario").one()
        inventory.permissions.append(Permission.query.filter_by(code="sales:read").one())
        db.session.commit()
        rbac_service.invalidate_cache()
        self.assertEqual(self.clients["inventario"].get("/sales/list").status_code, 200)
        sold = self.clients["vendedor"].post("/api/sales", json=self.sale_body())
        self.assertEqual(sold.status_code, 201, sold.get_json())
        before = (Sale.query.count(), Quote.query.count(), Order.query.count(), self.stock())

        self.app.config["DEMO_WITHDRAW_CUSTOMER_SALES"] = True
        rbac_service.invalidate_cache()
        self.assertTrue(rbac_service.customer_sales_withdrawn())
        self.assertTrue(WITHDRAWN_CUSTOMER_SALES.isdisjoint(rbac_service.permissions_for("admin")))
        self.assertTrue(WITHDRAWN_CUSTOMER_SALES.isdisjoint(rbac_service.permissions_for("vendedor")))

        for role in _ROLES:
            client = self.clients[role]
            for path in _PAGES:
                page = client.get(path)
                self.assertEqual(page.status_code, 403, f"{role} {path}")
                self.assertIn("No tiene permisos", page.get_data(as_text=True))
            for path in _READS:
                self.assertEqual(client.get(path).status_code, 403, f"{role} GET {path}")
            for method, path in _WRITES:
                response = getattr(client, method)(path, json={})
                self.assertEqual(response.status_code, 403, f"{role} {method} {path}")

        anonymous = self.app.test_client()
        for path in _PAGES:
            page = anonymous.get(path)
            self.assertEqual(page.status_code, 302, path)
            self.assertIn("/login", page.headers.get("Location", ""))
        for path in ("/api/sales", "/api/quotes", "/api/orders"):
            denied = anonymous.get(path)
            self.assertEqual(denied.status_code, 401, path)
            self.assertEqual(anonymous.post(path, json={}).status_code, 401, path)

        self.assertEqual(self.clients["vendedor"].get("/products").status_code, 200)
        self.assertEqual(self.clients["vendedor"].get("/sales/customers").status_code, 200)
        self.assertEqual(self.clients["admin"].get("/dashboard").status_code, 200)
        self.assertEqual(self.clients["asistente"].get("/dashboard").status_code, 200)
        self.assertEqual(self.clients["inventario"].get("/dashboard").status_code, 200)
        self.assertEqual(self.clients["soporte_ti"].get("/settings").status_code, 200)
        self.assertNotEqual(self.clients["asistente"].post("/api/supplier-quotes", json={}).status_code, 403)
        report = self.clients["admin"].get("/sales/reports")
        self.assertNotEqual(report.status_code, 403)
        search = self.clients["vendedor"].get("/api/shell/search", query_string={"q": "cl"})
        self.assertEqual(search.status_code, 200, search.get_data(as_text=True)[:200])
        self.assertNotIn("sales", search.get_json()["groups"])
        products = self.clients["vendedor"].get("/products").get_data(as_text=True)
        for href in ("/sales/new", "/sales/list", "/sales/orders", "/sales/quotes"):
            self.assertNotIn(f'href="{href}"', products)

        removed = rbac_service.revoke_withdrawn_customer_sales()
        self.assertGreater(removed, 0)
        self.assertEqual(rbac_service.revoke_withdrawn_customer_sales(), 0)
        for role in Role.query.all():
            self.assertTrue(role.permission_codes.isdisjoint(WITHDRAWN_CUSTOMER_SALES), role.slug)
        self.assertEqual(
            Permission.query.filter(Permission.code.in_(WITHDRAWN_CUSTOMER_SALES)).count(),
            len(WITHDRAWN_CUSTOMER_SALES),
        )
        self.assertEqual((Sale.query.count(), Quote.query.count(), Order.query.count(), self.stock()), before)
        admin_role = Role.query.filter_by(slug="admin").one()
        with self.assertRaises(ValidationError) as caught:
            rbac_service.update_role(
                admin_role.id,
                {"permissions": sorted(admin_role.permission_codes | {"sales:read"})},
                self.users["admin"],
            )
        self.assertEqual(caught.exception.status_code, 403)
        db.session.refresh(admin_role)
        self.assertNotIn("sales:read", admin_role.permission_codes)

    def sale_body(self):
        return {
            "customer_id": self.customer.id, "payment_method": "cash", "payment_condition": "cash",
            "cash_received": "10.00", "idempotency_key": str(uuid4()),
            "items": [{"product_id": self.product.id, "quantity": 1}],
        }


if __name__ == "__main__":
    unittest.main()
