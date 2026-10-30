"""Revisión de catálogos en SQLite desechable; sin dotenv, MySQL o puerto 5000.

Uso: python scripts/test_catalog_approval_review.py
--serve inicia únicamente esta fixture sintética en un puerto asignado por el SO.
"""
import sys
import unittest

from approval_test_support import IsolatedCase
from app.extensions import db
from app.models import Category, Product, ProductChange, StockMovement, User
from app.models.purchase import Supplier
from app.services import dashboard_service, rbac_service


class CatalogReviewTests(IsolatedCase):
    def payload(self, kind, suffix="1"):
        if kind == "products":
            return dict(code="PROP-" + suffix, name="Producto propuesto " + suffix,
                        category_id=self.product.category_id, unit="unidad", purchase_price=2, sale_price=3,
                        purchase_price_currency="USD", sale_price_currency="USD")
        if kind == "categories":
            return dict(name="Categoría propuesta " + suffix)
        return dict(name="Proveedor propuesto " + suffix, tax_id="PROP-" + suffix,
                    contact_name="Contacto sintético", phone="0000000000",
                    email="fixture@example.invalid", category="Prueba", is_active=True)

    def propose(self, kind, suffix="1"):
        result = self.clients["asistente"].post("/api/" + kind, json=self.payload(kind, suffix))
        self.assertEqual(result.status_code, 201, result.json)
        self.assertEqual(result.json["approval_status"], "pending")
        self.assertFalse(result.json["is_active"])
        return result.json

    def listing(self, kind, status):
        response = self.clients["admin"].get(f"/api/{kind}?approval_status={status}")
        self.assertEqual(response.status_code, 200, response.json)
        return response.json["items"]

    def test_assistant_proposes_admin_decides_inventory_denied_and_retries_conflict(self):
        before = self.stock(), StockMovement.query.count()
        for kind in ("products", "suppliers", "categories"):
            with self.subTest(kind=kind):
                row = self.propose(kind)
                path = f"/api/{kind}/{row['id']}"
                self.assertEqual(self.clients["admin"].get(path).status_code, 200)
                for role in ("asistente", "inventario"):
                    for decision in ("approve", "reject"):
                        result = self.clients[role].post(path + "/" + decision, json={"reason": "Prueba"})
                        self.assertEqual(result.status_code, 403, result.json)
                result = self.clients["admin"].post(path + "/approve", json={})
                self.assertEqual(result.status_code, 200, result.json)
                self.assertEqual(result.json["approval_status"], "approved")
                self.assertTrue(result.json["is_active"])
                for decision in ("approve", "reject"):
                    retry = self.clients["admin2"].post(path + "/" + decision, json={"reason": "Prueba"})
                    self.assertEqual(retry.status_code, 409, retry.json)
                rejected = self.propose(kind, "2")
                path = f"/api/{kind}/{rejected['id']}"
                self.assertEqual(self.clients["admin"].post(path + "/reject", json={}).status_code, 400)
                result = self.clients["admin"].post(path + "/reject", json={"reason": "No corresponde"})
                self.assertEqual(result.status_code, 200, result.json)
                self.assertEqual(result.json["approval_status"], "rejected")
                self.assertFalse(result.json["is_active"])
                self.assertEqual(result.json["rejection_reason"], "No corresponde")
                for decision in ("approve", "reject"):
                    self.assertEqual(self.clients["admin"].post(path + "/" + decision,
                                                              json={"reason": "Reintento"}).status_code, 409)
        self.assertEqual((self.stock(), StockMovement.query.count()), before)

    def test_all_approval_filters_and_invalid_values(self):
        for kind in ("products", "suppliers", "categories"):
            pending = self.propose(kind)
            rejected = self.propose(kind, "2")
            self.clients["admin"].post(f"/api/{kind}/{rejected['id']}/reject", json={"reason": "Prueba"})
            self.assertEqual([row["id"] for row in self.listing(kind, "pending")], [pending["id"]])
            self.assertEqual([row["id"] for row in self.listing(kind, "rejected")], [rejected["id"]])
            self.assertTrue(all(row["approval_status"] == "approved" for row in self.listing(kind, "approved")))
            self.assertEqual(len(self.listing(kind, "all")), 3)
            self.assertEqual(self.clients["admin"].get(f"/api/{kind}?approval_status=unknown").status_code, 400)

    def test_exports_keep_the_approval_filter(self):
        for kind in ("products", "suppliers", "categories"):
            proposed = self.propose(kind)
            if kind == "products":
                response = self.clients["admin"].post("/api/products/export", json={
                    "format": "csv", "filters": {"approval_status": "pending"}})
            else:
                response = self.clients["admin"].get(f"/api/{kind}/export?format=csv&approval_status=pending")
            self.assertEqual(response.status_code, 200, response.get_json(silent=True))
            text = response.data.decode("utf-8-sig")
            self.assertIn(proposed["name"], text)
            original = {"products": "Producto sintético", "suppliers": "Proveedor sintético",
                        "categories": "Pruebas aisladas"}[kind]
            self.assertNotIn(original, text)

    def test_proposer_cannot_decide_even_after_becoming_admin(self):
        rows = {kind: self.propose(kind) for kind in ("products", "suppliers", "categories")}
        self.users["asistente"].role = "admin"
        db.session.commit()
        rbac_service.invalidate_cache()
        for kind, row in rows.items():
            path = f"/api/{kind}/{row['id']}"
            for decision in ("approve", "reject"):
                response = self.clients["asistente"].post(path + "/" + decision, json={"reason": "Prueba"})
                self.assertEqual(response.status_code, 403, response.json)
            db.session.expire_all()
            self.assertEqual(self.clients["admin"].get(path).json["approval_status"], "pending")

    def test_pending_product_changes_share_filter_and_dashboard_count(self):
        creation = self.propose("products")
        result = self.clients["asistente"].put(f"/api/products/{self.product.id}", json={"name": "Cambio propuesto"})
        self.assertEqual(result.status_code, 200, result.json)
        pending = self.listing("products", "pending")
        self.assertEqual({row["id"] for row in pending}, {creation["id"], self.product.id})
        self.assertEqual(self.listing("products", "approved"), [])
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(self.users["admin"])
            items = dashboard_service.pending_proposals("admin")
        self.assertEqual(items[0]["count"], 2)
        self.assertEqual(items[0]["href"], "/products?approval_status=pending")
        self.assertEqual(db.session.get(Product, self.product.id).name, "Producto sintético")
        detail = self.clients["admin"].get(f"/api/products/{self.product.id}").json
        self.assertTrue(detail["can_review_change"])
        result = self.clients["admin"].post(f"/api/products/{self.product.id}/approve",
            json={"proposal_id": detail["pending_change"]["id"]})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(len(self.listing("products", "pending")), 1)

    def test_product_approval_filter_combines_current_category_operational_state_and_search(self):
        creation = self.propose("products")
        response = self.clients["asistente"].patch(f"/api/products/{self.product.id}",
                                                 json={"name": "Nombre futuro"})
        self.assertEqual(response.status_code, 200, response.json)
        category = self.product.category_id
        for status, search, expected in (
            ("inactivo", "PROP-1", {creation["id"]}),
            ("disponible", "TEST-01", {self.product.id}),
            ("disponible", "Nombre futuro", set()),
            ("agotado", "TEST-01", set()),
        ):
            with self.subTest(status=status, search=search):
                result = self.clients["admin"].get("/api/products", query_string={
                    "approval_status": "pending", "category_id": category,
                    "status": status, "search": search, "page": 1, "per_page": 8})
                self.assertEqual(result.status_code, 200, result.json)
                self.assertEqual({row["id"] for row in result.json["items"]}, expected)
                self.assertEqual(result.json["count"], len(expected))
        other = Category(name="Otra categoría")
        db.session.add(other)
        db.session.commit()
        result = self.clients["admin"].get("/api/products", query_string={
            "approval_status": "pending", "category_id": other.id, "status": "inactivo", "search": "PROP-1"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["items"], [])

    def test_product_approved_and_rejected_filters_include_inactive_and_combine_filters(self):
        approved = self.propose("products", "approved")
        self.clients["admin"].post(f"/api/products/{approved['id']}/approve", json={})
        change = self.clients["asistente"].patch(f"/api/products/{approved['id']}", json={"is_active": False}).json
        self.clients["admin"].post(f"/api/products/{approved['id']}/approve",
                                   json={"proposal_id": change["pending_change"]["id"]})
        rejected = self.propose("products", "rejected")
        self.clients["admin"].post(f"/api/products/{rejected['id']}/reject", json={"reason": "Prueba"})
        self.propose("products", "pending")
        self.assertEqual({row["id"] for row in self.listing("products", "approved")},
                         {self.product.id, approved["id"]})
        self.assertEqual({row["id"] for row in self.listing("products", "rejected")}, {rejected["id"]})
        for approval, row in (("approved", approved), ("rejected", rejected)):
            response = self.clients["admin"].get("/api/products", query_string={
                "approval_status": approval, "status": "inactivo", "category_id": self.product.category_id,
                "search": row["code"]})
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual([item["id"] for item in response.json["items"]], [row["id"]])

    def test_pending_creation_and_change_detail_show_author_date_and_current_values(self):
        creation = self.propose("products")
        detail = self.clients["admin"].get(f"/api/products/{creation['id']}").json
        self.assertEqual(detail["proposal_author"], "asistente")
        self.assertEqual(detail["proposed_by_user_id"], self.users["asistente"].id)
        self.assertTrue(detail["created_at"])
        self.assertTrue(detail["can_review_creation"])
        self.assertIsNone(detail["pending_change"])
        response = self.clients["asistente"].patch(f"/api/products/{self.product.id}",
                                                 json={"name": "Nombre futuro", "minimum_stock": 5})
        self.assertEqual(response.status_code, 200, response.json)
        detail = self.clients["admin"].get(f"/api/products/{self.product.id}").json
        self.assertEqual(detail["proposal_author"], "asistente")
        self.assertEqual(detail["name"], "Producto sintético")
        self.assertEqual(detail["pending_change"]["changes"]["name"], "Nombre futuro")
        self.assertEqual(detail["pending_change"]["proposed_by_user_id"], self.users["asistente"].id)
        self.assertTrue(detail["pending_change"]["created_at"])
        self.assertTrue(detail["can_review_change"])
        self.assertEqual(self.stock(), 10)

    def test_product_pending_pagination_and_zero_results(self):
        creation = self.propose("products")
        self.clients["asistente"].patch(f"/api/products/{self.product.id}", json={"name": "Nombre futuro"})
        ids = []
        for page in (1, 2):
            response = self.clients["admin"].get("/api/products", query_string={
                "approval_status": "pending", "category_id": self.product.category_id,
                "search": "Producto", "page": page, "per_page": 1})
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(response.json["pagination"]["pages"], 2)
            ids.extend(item["id"] for item in response.json["items"])
        self.assertEqual(set(ids), {creation["id"], self.product.id})
        for approval in ("pending", "approved", "rejected"):
            result = self.clients["admin"].get("/api/products", query_string={
                "approval_status": approval, "search": "Sin coincidencia sintética", "page": 1, "per_page": 8})
            self.assertEqual(result.status_code, 200, result.json)
            self.assertEqual(result.json["count"], 0)
            self.assertEqual(result.json["items"], [])
            self.assertEqual(result.json["pagination"]["pages"], 0)

    def test_supplier_edits_return_to_review_and_cannot_activate_pending(self):
        path = f"/api/suppliers/{self.supplier.id}"
        body = self.payload("suppliers") | {"tax_id": self.supplier.tax_id}
        result = self.clients["asistente"].patch(path, json=body)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json["approval_status"], "pending")
        self.assertFalse(result.json["is_active"])
        pending = result.json
        self.assertEqual(self.clients["asistente"].post(path + "/status", json={}).status_code, 403)
        # Admin decide propuestas; activar directamente está prohibido en cualquier estado.
        self.assertEqual(self.clients["admin"].post(path + "/status", json={}).status_code, 403)
        self.assertEqual(self.clients["admin"].patch(path, json=body).status_code, 403)
        self.assertEqual(self.clients["admin"].get(path).json, pending)
        result = self.clients["admin"].post(path + "/approve", json={})
        self.assertEqual(result.status_code, 200, result.json)
        for actor in ("asistente", "inventario"):
            for action in ("status", "preferred"):
                self.assertEqual(self.clients[actor].post(path + "/" + action, json={}).status_code, 403)
            self.assertEqual(self.clients[actor].delete(path, json={}).status_code, 403)
        result = self.clients["asistente"].patch(path, json=body | {"name": "Otra propuesta"})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json["approval_status"], "pending")
        self.assertIsNone(result.json["approved_by_user_id"])
        self.assertIsNone(result.json["approved_at"])

    def test_pending_supplier_owned_by_proposer_and_rejected_edit_resubmits(self):
        proposal = self.propose("suppliers")
        other = User(name="Otro Asistente", username="asistente2", email="asistente2@example.invalid", role="asistente")
        db.session.add(other)
        db.session.commit()
        client = self.client_for(other)
        path = f"/api/suppliers/{proposal['id']}"
        self.assertEqual(client.patch(path, json=self.payload("suppliers")).status_code, 403)
        self.assertEqual(self.clients["asistente"].patch(path, json=self.payload("suppliers")).status_code, 200)
        self.clients["admin"].post(path + "/reject", json={"reason": "Corregir"})
        response = self.clients["asistente"].patch(path, json=self.payload("suppliers"))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["approval_status"], "pending")
        self.assertIsNone(response.json["rejection_reason"])

    def test_category_admin_cannot_edit_proposal_data_assistant_edit_requires_review(self):
        path = f"/api/categories/{self.product.category_id}"
        self.assertEqual(self.clients["admin"].put(path, json={"name": "Edición directa"}).status_code, 403)
        self.assertEqual(self.clients["admin"].put(path, json={"is_active": False}).status_code, 403)
        response = self.clients["asistente"].put(path, json={"description": "Cambio propuesto"})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["approval_status"], "approved")
        self.assertEqual(response.json["pending_change"]["status"], "pending")
        self.assertEqual(response.json["description"], None)
        self.assertEqual(self.clients["admin"].put(path, json={"is_active": True}).status_code, 403)

    def test_pages_filters_navigation_dashboard_and_supplier_own_permissions(self):
        self.propose("products")
        self.propose("suppliers")
        self.propose("categories")
        for path in ("/products", "/suppliers", "/categories"):
            page = self.clients["admin"].get(path + "?approval_status=pending")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Estado de aprobación", page.text)
            for status in ("pending", "approved", "rejected"):
                self.assertIn(f'value="{status}"', page.text)
        admin = self.clients["admin"].get("/dashboard")
        self.assertEqual(admin.status_code, 200)
        for label, path in (("Productos pendientes", "/products"), ("Proveedores pendientes", "/suppliers"),
                            ("Categorías pendientes", "/categories")):
            self.assertIn(label, admin.text)
            self.assertIn(path + "?approval_status=pending", admin.text)
        for role in ("asistente", "inventario"):
            self.assertNotIn("Revisar propuestas pendientes", self.clients[role].get("/dashboard").text)
        # Supplier controls follow supplier permissions, independently of purchase creation.
        from app.models import Permission, Role
        role = Role.query.filter_by(slug="asistente").one()
        role.permissions.remove(Permission.query.filter_by(code="purchases:write").one())
        db.session.commit()
        rbac_service.invalidate_cache()
        self.assertIn('id="sp-new"', self.clients["asistente"].get("/suppliers").text)
        self.assertNotIn('id="sp-new"', self.clients["admin"].get("/suppliers").text)


def serve_fixture():
    """Servidor opcional de evidencia visual: solo datos de prueba y loopback."""
    from flask import abort, redirect, session
    from werkzeug.serving import make_server
    import logging
    from app.services import session_service

    fixture = CatalogReviewTests()
    fixture.setUp()
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    @fixture.app.get("/_fixture/session/<role>")
    def fixture_session(role):
        if role not in fixture.users:
            abort(404)
        session.clear()
        session_service.open_session(db.session.get(User, fixture.users[role].id))
        return redirect("/dashboard" if role != "vendedor" else "/products")

    try:
        for kind in ("products", "suppliers", "categories"):
            for suffix in ("1", "2", "3"):
                row = fixture.propose(kind, suffix)
                if suffix == "2":
                    fixture.clients["admin"].post(f"/api/{kind}/{row['id']}/approve", json={})
                elif suffix == "3":
                    fixture.clients["admin"].post(f"/api/{kind}/{row['id']}/reject", json={"reason": "Prueba aislada"})
        fixture.clients["asistente"].put(f"/api/products/{fixture.product.id}", json={"name": "Modificación pendiente"})

        with make_server("127.0.0.1", 0, fixture.app) as server:
            print(f"BASE_URL=http://127.0.0.1:{server.server_port}", flush=True)
            server.serve_forever()
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    if sys.argv[1:] == ["--serve"]:
        serve_fixture()
    else:
        unittest.main()
