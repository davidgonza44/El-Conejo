"""Regresión de Proveedores por rol con SQLite en memoria, sin MySQL ni puertos."""
import json
import re
import unittest

from approval_test_support import IsolatedCase
from app.extensions import db
from app.models import Permission, Role, User
from app.services import rbac_service
from docker_support.fix_demo_supplier_access import grant_read
from migrate_tutor_cycle import GRANTS


def payload(name="Proveedor de prueba"):
    return dict(name=name, tax_id="TEST-APPROVAL-01", contact_name="Contacto de prueba",
                phone="0000000000", email="test@example.invalid", category="Pruebas", is_active=True)


class SupplierAccessTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        support = User(name="soporte_ti", username="soporte_ti", email="support@example.invalid", role="soporte_ti")
        db.session.add(support)
        db.session.commit()
        self.clients["soporte_ti"] = self.client_for(support)

    def page_context(self, client):
        response = client.get("/suppliers")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        match = re.search(r'<script[^>]*id="sp-context"[^>]*>(.*?)</script>', html, re.DOTALL)
        return html, json.loads(match.group(1))

    def proposal(self):
        response = self.clients["asistente"].post("/api/suppliers", json=payload())
        self.assertEqual(response.status_code, 201, response.get_json())
        row = response.get_json()
        self.assertEqual(row["approval_status"], "pending")
        self.assertFalse(row["is_active"])
        return row["id"]

    def assert_no_direct_changes(self, supplier_id):
        client = self.clients["admin"]
        path = f"/api/suppliers/{supplier_id}"
        before = client.get(path).get_json()
        self.assertEqual(client.post("/api/suppliers", json=payload("Directo")).status_code, 403)
        self.assertEqual(client.patch(path, json=payload("Directo")).status_code, 403)
        for action in ("preferred", "status"):
            self.assertEqual(client.post(path + "/" + action, json={}).status_code, 403)
        self.assertEqual(client.delete(path, json={}).status_code, 403)
        self.assertEqual(client.get(path).get_json(), before)

    def assert_decisions_return(self, path, roles, status_code):
        for role in roles:
            for action in ("approve", "reject"):
                with self.subTest(role=role, action=action):
                    response = self.clients[role].post(path + "/" + action, json={"reason": "Prueba"})
                    self.assertEqual(response.status_code, status_code, response.get_json())

    def test_menu_filters_and_permissions(self):
        for role in ("admin", "asistente"):
            with self.subTest(role=role):
                html, context = self.page_context(self.clients[role])
                self.assertIn('href="/suppliers"', html.split("</aside>")[0])
                self.assertIn('name="approval_status"', html)
                for value, label in (("", "Todos"), ("pending", "Pendiente"),
                                     ("approved", "Aprobado"), ("rejected", "Rechazado")):
                    self.assertIn(f'<option value="{value}">{label}</option>', html)
                self.assertEqual(context["canWrite"], role == "asistente")
                self.assertEqual(context["canApprove"], role == "admin")
        for role in ("inventario", "vendedor", "soporte_ti"):
            client = self.clients[role]
            for path in ("/suppliers", "/api/suppliers", f"/api/suppliers/{self.supplier.id}", "/api/suppliers/export"):
                with self.subTest(role=role, path=path):
                    self.assertEqual(client.get(path).status_code, 403)

    def test_pending_approve_and_approved_read_only(self):
        supplier_id = self.proposal()
        path = f"/api/suppliers/{supplier_id}"
        admin = self.clients["admin"]
        pending = admin.get("/api/suppliers?approval_status=pending").get_json()["items"]
        self.assertIn(supplier_id, [row["id"] for row in pending])
        self.assert_no_direct_changes(supplier_id)
        self.assertEqual(self.clients["asistente"].post(path + "/approve", json={}).status_code, 403)
        approved = admin.post(path + "/approve", json={})
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.get_json()["approval_status"], "approved")
        self.assert_no_direct_changes(supplier_id)
        self.assertEqual(admin.post(path + "/approve", json={}).status_code, 409)

    def test_assistant_proposes_modification_and_admin_rejects(self):
        supplier_id = self.proposal()
        path = f"/api/suppliers/{supplier_id}"
        admin = self.clients["admin"]
        self.assertEqual(admin.post(path + "/approve", json={}).status_code, 200)
        proposed = self.clients["asistente"].patch(path, json=payload("Cambio propuesto"))
        self.assertEqual(proposed.status_code, 200)
        self.assertEqual(proposed.get_json()["approval_status"], "pending")
        self.assertFalse(proposed.get_json()["is_active"])
        self.assertEqual(admin.post(path + "/reject", json={}).status_code, 400)
        rejected = admin.post(path + "/reject", json={"reason": "Datos por verificar"})
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.get_json()["approval_status"], "rejected")
        self.assertEqual(rejected.get_json()["rejection_reason"], "Datos por verificar")
        rows = admin.get("/api/suppliers?approval_status=rejected").get_json()["items"]
        self.assertIn(supplier_id, [row["id"] for row in rows])

    def test_authorization_denials_and_decided_state_conflicts(self):
        admin = self.clients["admin"]
        for state in ("pending", "approved", "rejected"):
            with self.subTest(state=state):
                body = payload(state) | {"tax_id": "TEST-STATE-" + state.upper()}
                created = self.clients["asistente"].post("/api/suppliers", json=body)
                self.assertEqual(created.status_code, 201, created.get_json())
                path = f"/api/suppliers/{created.get_json()['id']}"
                if state != "pending":
                    action = "approve" if state == "approved" else "reject"
                    decided = admin.post(path + "/" + action, json={"reason": "Prueba aislada"})
                    self.assertEqual(decided.status_code, 200, decided.get_json())
                before = admin.get(path).get_json()
                self.assertEqual(before["approval_status"], state)
                self.assert_no_direct_changes(before["id"])
                # Un rol sin autorización recibe 403 incluso cuando ya hubo decisión.
                self.assert_decisions_return(path, ("asistente", "inventario", "soporte_ti", "vendedor"), 403)
                if state != "pending":
                    # Admin sí puede decidir; el estado decidido impide esta transición.
                    self.assert_decisions_return(path, ("admin",), 409)
                self.assertEqual(admin.get(path).get_json(), before)

    def test_repair_missing_persisted_read_is_minimal_and_idempotent(self):
        for slug in ("admin", "asistente"):
            self.assertIn("suppliers:read", GRANTS[slug])
            role = Role.query.filter_by(slug=slug).one()
            role.permissions = [p for p in role.permissions if p.code != "suppliers:read"]
        db.session.commit()
        rbac_service.invalidate_cache()
        before = {r.slug: r.permission_codes for r in Role.query.all()}
        for slug in ("admin", "asistente"):
            self.assertEqual(self.clients[slug].get("/suppliers").status_code, 403)
        with db.engine.begin() as connection:
            self.assertEqual(grant_read(connection), 2)
            self.assertEqual(grant_read(connection), 0)
        db.session.expire_all()
        rbac_service.invalidate_cache()
        for role in Role.query.all():
            expected = before[role.slug] | ({"suppliers:read"} if role.slug in ("admin", "asistente") else set())
            self.assertEqual(role.permission_codes, expected)
        for slug in ("admin", "asistente"):
            self.assertEqual(self.clients[slug].get("/suppliers").status_code, 200)

    def test_inventory_denied_even_with_stale_supplier_grants(self):
        role = Role.query.filter_by(slug="inventario").one()
        role.permissions += Permission.query.filter(Permission.code.in_(
            ("suppliers:read", "suppliers:propose", "suppliers:approve"))).all()
        db.session.commit()
        rbac_service.invalidate_cache()
        client = self.clients["inventario"]
        self.assertEqual(client.get("/suppliers").status_code, 403)
        self.assertEqual(client.get("/api/suppliers").status_code, 403)
        self.assertEqual(client.post("/api/suppliers", json=payload()).status_code, 403)
        self.assertEqual(client.post(f"/api/suppliers/{self.supplier.id}/approve", json={}).status_code, 403)

    def test_repair_missing_permission_catalog_entry(self):
        permission = Permission.query.filter_by(code="suppliers:read").one()
        for role in Role.query.all():
            role.permissions = [p for p in role.permissions if p != permission]
        db.session.flush()
        db.session.delete(permission)
        db.session.commit()
        before = {r.slug: r.permission_codes for r in Role.query.all()}
        with db.engine.begin() as connection:
            self.assertEqual(grant_read(connection), 2)
            self.assertEqual(grant_read(connection), 0)
        db.session.expire_all()
        for role in Role.query.all():
            expected = before[role.slug] | ({"suppliers:read"} if role.slug in ("admin", "asistente") else set())
            self.assertEqual(role.permission_codes, expected)
        self.assertEqual(Permission.query.filter_by(code="suppliers:read").count(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
