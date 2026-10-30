"""Alcance DEMO tras las correcciones del tutor (5 oct 2026): ventas cerradas, reportes y compras vigentes.

Configuración DEMO (DEMO_WITHDRAW_CUSTOMER_SALES) sobre SQLite en memoria. No contacta el servidor 5001:
una prueba en esta copia no acredita el despliegue. Reutiliza las rutas de test_demo_withdrawn_customer_sales.
"""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from approval_test_support import LinkedSupplierCase
from app.extensions import db
from app.models import Permission, Role, Sale, User
from app.services import rbac_service
from app.utils.permissions import WITHDRAWN_CUSTOMER_SALES
from test_demo_withdrawn_customer_sales import _PAGES, _READS, _WRITES

ROLES = ("admin", "inventario", "asistente", "vendedor", "soporte_ti")
REPORTS = ("/api/reports/dashboard-summary", "/api/reports/entries-vs-exits", "/api/reports/top-products-by-exits",
           "/api/reports/delivery-notes-by-period")


class DemoScopeTests(LinkedSupplierCase):
    def setUp(self):
        super().setUp()
        support = User(name="Soporte sintético", username="soporte", email="soporte@example.invalid", role="soporte_ti")
        db.session.add(support)
        db.session.commit()
        self.users["soporte_ti"] = support
        self.clients["soporte_ti"] = self.client_for(support)
        # Legacy fixture: commercial grants inherited by an old installation.
        for slug in ("vendedor", "admin", "inventario"):
            role = Role.query.filter_by(slug=slug).one()
            for permission in Permission.query.filter(Permission.code.in_(WITHDRAWN_CUSTOMER_SALES)).all():
                if permission not in role.permissions:
                    role.permissions.append(permission)
        db.session.commit()
        self.app.config["DEMO_WITHDRAW_CUSTOMER_SALES"] = True
        rbac_service.invalidate_cache()

    def test_commercial_pages_apis_and_writes_stay_denied(self):
        sales = Sale.query.count()
        for role in ROLES:
            self.assertTrue(WITHDRAWN_CUSTOMER_SALES.isdisjoint(rbac_service.permissions_for(role)), role)
            client = self.clients[role]
            for path in (*_PAGES, *_READS):
                self.assertEqual(client.get(path).status_code, 403, f"{role} GET {path}")
            for method, path in _WRITES:
                self.assertEqual(getattr(client, method)(path, json={}).status_code, 403, f"{role} {method} {path}")
        self.assertEqual(Sale.query.count(), sales)

    def test_historical_reports_remain_readable_without_sales_permissions(self):
        for role in ("admin", "inventario"):
            for path in REPORTS:
                response = self.clients[role].get(path)
                self.assertEqual(response.status_code, 200, f"{role} {path} {response.get_data(as_text=True)[:200]}")
        self.assertNotEqual(self.clients["admin"].get("/sales/reports").status_code, 403)
        for role in ("vendedor", "soporte_ti"):
            self.assertEqual(self.clients[role].get(REPORTS[0]).status_code, 403, role)
        purchases = self.clients["asistente"].get("/purchases").get_data(as_text=True)
        for href in ("/sales/new", "/sales/list", "/sales/orders", "/sales/quotes"):
            self.assertNotIn(f'href="{href}"', purchases)

    def test_corrected_purchase_flow_works_in_demo_and_opens_no_commercial_path(self):
        today = datetime.now(ZoneInfo("America/Caracas")).date().isoformat()
        inventory, assistant, admin = self.clients["inventario"], self.clients["asistente"], self.clients["admin"]
        row = inventory.post("/api/replenishment-requests", json={"items": [{"product_id": self.product.id, "quantity": 10}]}).get_json()
        self.assertEqual(assistant.post(f"/api/replenishment-requests/{row['id']}/start", json={}).status_code, 200)
        quote = assistant.post(f"/api/replenishment-requests/{row['id']}/quotes", json={
            "supplier_id": self.supplier.id, "currency": "VES", "issued_on": today, "valid_until": "2099-01-01",
            "items": [{"product_id": self.product.id, "unit_price": "10.00"}]}).get_json()
        prepared = assistant.post(f"/api/replenishment-requests/{row['id']}/prepare", json={"supplier_quote_id": quote["id"]})
        self.assertEqual(prepared.status_code, 200, prepared.get_json())
        order_id = prepared.get_json()["purchase_order"]["id"]
        approved = admin.post(f"/api/purchases/{order_id}/approve", json={}).get_json()
        received = inventory.post(f"/api/purchases/{order_id}/receptions", json={
            "received_at": today, "items": [{"line_id": approved["items"][0]["id"], "quantity": 6}],
            "confirm_partial_closure": True, "closure_reason": "Proveedor sintético sin existencia del resto"})
        self.assertEqual(received.status_code, 200, received.get_json())
        shortage = inventory.post(f"/api/replenishment-requests/from-order/{order_id}", json={})
        self.assertEqual(shortage.status_code, 201, shortage.get_json())
        for role in ROLES:
            self.assertTrue(WITHDRAWN_CUSTOMER_SALES.isdisjoint(rbac_service.permissions_for(role)), role)
        self.assertEqual(Sale.query.count(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
