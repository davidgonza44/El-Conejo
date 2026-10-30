"""API, RBAC, migración y paginación sobre SQLite desechable; MySQL prohibido."""
import unittest
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from approval_test_support import IsolatedCase, IsolatedConfig
from app import create_app
from app.extensions import db
from app.models import Category, User
from app.services import category_service, rbac_service
from app.services.exceptions import ConflictError
from app.utils.schema import reset_cache


class CategoryApprovalTests(IsolatedCase):
    def propose(self, name="Categoría propuesta"):
        response = self.clients["asistente"].post("/api/categories", json={"name": name, "is_active": True,
            "approval_status": "approved", "approved_by_user_id": self.users["asistente"].id})
        self.assertEqual(response.status_code, 201, response.get_json())
        category = response.get_json()
        self.assertEqual(category["approval_status"], "pending")
        self.assertFalse(category["is_active"])
        self.assertEqual(category["proposed_by_user_id"], self.users["asistente"].id)
        self.assertIsNone(category["approved_by_user_id"])
        return category

    def product_data(self, category_id):
        return dict(code="PROPOSED-01", name="Producto propuesto", category_id=category_id,
                    unit="unidad", purchase_price=1, sale_price=2)

    def test_assistant_proposes_and_inventory_admin_cannot_create(self):
        self.propose()
        for role in ("inventario", "admin", "vendedor"):
            self.assertEqual(self.clients[role].post("/api/categories", json={"name": role}).status_code, 403)
        self.assertEqual(self.clients["inventario"].put(f"/api/categories/{self.product.category_id}", json={"name": "X"}).status_code, 403)

    def test_no_self_approval_even_after_role_change_or_permission_grant(self):
        category = self.propose()
        path = f"/api/categories/{category['id']}/approve"
        self.assertEqual(self.clients["asistente"].post(path).status_code, 403)
        from app.models import Permission, Role
        role = Role.query.filter_by(slug="asistente").one()
        role.permissions.append(Permission.query.filter_by(code="categories:approve").one())
        db.session.commit()
        self.assertEqual(self.clients["asistente"].post(path).status_code, 403)
        self.users["asistente"].role = "admin"
        db.session.commit()
        rbac_service.invalidate_cache()
        self.assertEqual(self.clients["asistente"].post(path).status_code, 403)

    def test_admin_approves_and_repeated_decisions_conflict(self):
        category = self.propose()
        path = f"/api/categories/{category['id']}"
        approved = self.clients["admin"].post(path + "/approve")
        self.assertEqual(approved.status_code, 200, approved.get_json())
        self.assertEqual(approved.get_json()["approval_status"], "approved")
        self.assertTrue(approved.get_json()["is_active"])
        self.assertEqual(approved.get_json()["approved_by_user_id"], self.users["admin"].id)
        self.assertEqual(self.clients["admin2"].post(path + "/approve").status_code, 409)
        self.assertEqual(self.clients["admin2"].post(path + "/reject", json={"reason": "Otro"}).status_code, 409)

    def test_admin_rejects_and_pending_rejected_inactive_are_not_usable(self):
        category = self.propose()
        path = f"/api/categories/{category['id']}"
        self.assertEqual(self.clients["admin"].post(path + "/reject", json={}).status_code, 400)
        result = self.clients["admin"].post(path + "/reject", json={"reason": "No corresponde al catálogo"})
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(result.get_json()["approval_status"], "rejected")
        self.assertFalse(result.get_json()["is_active"])
        self.assertEqual(self.clients["admin"].post(path + "/reject", json={"reason": "Otra"}).status_code, 409)
        self.assertEqual(self.clients["asistente"].post("/api/products", json=self.product_data(category["id"])).status_code, 400)
        self.assertEqual(self.clients["asistente"].put(path, json={"is_active": True}).status_code, 400)
        self.assertEqual(self.clients["admin"].put(path, json={"is_active": True}).status_code, 403)

    def test_product_creation_edit_and_options_require_approved_active_category(self):
        category = self.propose()
        category_id = category["id"]
        assistant = self.clients["asistente"]
        data = self.product_data(category_id)
        self.assertEqual(assistant.post("/api/products", json=data).status_code, 400)
        self.assertEqual(assistant.put(f"/api/products/{self.product.id}", json={"category_id": category_id}).status_code, 400)
        ids = [item["id"] for item in assistant.get("/api/categories?status=active").get_json()["items"]]
        self.assertNotIn(category_id, ids)
        self.assertEqual(self.clients["admin"].post(f"/api/categories/{category_id}/approve").status_code, 200)
        ids = [item["id"] for item in assistant.get("/api/categories?status=active").get_json()["items"]]
        self.assertIn(category_id, ids)
        self.assertEqual(assistant.post("/api/products", json=data).status_code, 201)
        self.assertEqual(assistant.put(f"/api/products/{self.product.id}", json={"category_id": category_id}).status_code, 200)
        # Resolver el cambio antes de probar otra edición: una propuesta pendiente devuelve 409.
        proposal = assistant.get(f"/api/products/{self.product.id}").get_json()["pending_change"]
        self.assertEqual(self.clients["admin"].post(f"/api/products/{self.product.id}/reject",
            json={"proposal_id": proposal["id"], "reason": "Continuar prueba de categoría inactiva"}).status_code, 200)
        change = assistant.put(f"/api/categories/{category_id}", json={"is_active": False}).get_json()["pending_change"]
        self.assertEqual(self.clients["admin"].post(f"/api/categories/{category_id}/approve",
            json={"proposal_id": change["id"]}).status_code, 200)
        self.assertEqual(assistant.put(f"/api/products/{self.product.id}", json={"category_id": category_id}).status_code, 400)

    def test_duplicates_equivalent_names_and_database_uniqueness(self):
        self.propose("  Herramientas   Eléctricas  ")
        for name in ("HERRAMIENTAS ELÉCTRICAS", "herramientas electricas", "Herramientas-Eléctricas", "Herramientas＿Eléctricas"):
            response = self.clients["asistente"].post("/api/categories", json={"name": name})
            self.assertEqual(response.status_code, 409, (name, response.get_json()))
        with patch.object(category_service, "_validate_name", return_value="herramientas eléctricas"):
            response = self.clients["asistente"].post("/api/categories", json={"name": "Race"})
            self.assertEqual(response.status_code, 409)
        self.assertEqual(Category.query.count(), 2)

    def test_duplicate_proposes_and_assistant_edits_need_approval(self):
        category_id = self.product.category_id
        duplicate = self.clients["asistente"].post(f"/api/categories/{category_id}/duplicate").get_json()
        self.assertEqual(duplicate["approval_status"], "pending")
        self.assertFalse(duplicate["is_active"])
        self.assertEqual(self.clients["admin"].post(f"/api/categories/{category_id}/duplicate").status_code, 403)
        changed = self.clients["asistente"].put(f"/api/categories/{category_id}", json={"description": "Propuesta de cambio"})
        self.assertEqual(changed.status_code, 200, changed.get_json())
        self.assertEqual(changed.get_json()["approval_status"], "approved")
        self.assertTrue(changed.get_json()["is_active"])
        self.assertEqual(changed.get_json()["pending_change"]["status"], "pending")
        self.assertIsNone(changed.get_json()["description"])
        self.assertEqual(self.clients["asistente"].put(f"/api/categories/{category_id}", json={"approval_status": "approved"}).status_code, 409)

    def test_conditional_decision_does_not_overwrite_a_concurrent_winner(self):
        category = self.propose()
        original_get = category_service.get_category_or_404
        def stale_read(category_id):
            record = original_get(category_id)
            # Otra conexión decidió después de la lectura: conserva el objeto ORM obsoleto.
            db.session.execute(db.update(Category).where(Category.id == category_id).values(
                approval_status="rejected", is_active=False, approved_by_user_id=self.users["admin2"].id,
                rejection_reason="Ganó la otra decisión"), execution_options={"synchronize_session": False})
            return record
        with patch.object(category_service, "get_category_or_404", side_effect=stale_read):
            response = self.clients["admin"].post(f"/api/categories/{category['id']}/approve")
        self.assertEqual(response.status_code, 409, response.get_json())

    def test_pagination_ten_and_six_and_last_page_after_filter(self):
        db.session.add_all(Category(name=f"Categoría {i:02}") for i in range(9))
        db.session.commit()
        client = self.clients["asistente"]
        first = client.get("/api/categories?page=1&per_page=10").get_json()
        self.assertEqual((len(first["items"]), first["count"], first["pagination"]["pages"]), (10, 10, 1))
        self.assertFalse(first["pagination"]["has_next"])
        first = client.get("/api/categories?page=1&page_size=6").get_json()
        second = client.get("/api/categories?page=2&per_page=6").get_json()
        self.assertEqual((len(first["items"]), len(second["items"])), (6, 4))
        self.assertEqual(first["pagination"]["pages"], 2)
        self.assertTrue(first["pagination"]["has_next"])
        self.assertFalse(second["pagination"]["has_next"])
        self.assertFalse({item["id"] for item in first["items"]} & {item["id"] for item in second["items"]})
        filtered = client.get("/api/categories?page=99&per_page=6&search=Pruebas").get_json()
        self.assertEqual((filtered["pagination"]["page"], len(filtered["items"])), (1, 1))

    def test_filters_export_existing_approved_and_buttons(self):
        self.assertEqual(self.product.category.approval_status, "approved")
        self.propose()
        assistant = self.clients["asistente"]
        self.assertEqual(assistant.get("/api/categories?status=pending").get_json()["count"], 1)
        csv = assistant.get("/api/categories/export?format=csv&status=pending").get_data(as_text=True)
        self.assertIn("Pendientes", csv)
        self.assertNotIn("Pruebas aisladas", csv)
        for role in ("admin", "inventario"):
            html = self.clients[role].get("/categories").get_data(as_text=True)
            self.assertNotIn(">Nueva categoría<", html)
        self.assertIn(">Nueva categoría<", assistant.get("/categories").get_data(as_text=True))

    def test_reference_mode_keeps_fixture_and_never_writes_categories(self):
        self.app.debug = True
        before = [(row.id, row.name, row.approval_status) for row in Category.query.all()]
        page = self.clients["admin"].get("/categories?ref=1")
        self.assertEqual(page.status_code, 200)
        self.assertIn("cl-fixture", page.get_data(as_text=True))
        self.assertNotIn("ix_categories_live.js", page.get_data(as_text=True))
        self.assertEqual(before, [(row.id, row.name, row.approval_status) for row in Category.query.all()])


class ConcurrentCategoryTests(unittest.TestCase):
    def test_two_administrators_with_independent_sessions_only_one_decision_wins(self):
        from flask_login import login_user

        with tempfile.TemporaryDirectory(prefix="category-concurrency-") as folder:
            class Config(IsolatedConfig):
                SQLALCHEMY_DATABASE_URI = "sqlite:///" + (Path(folder) / "test.sqlite").as_posix()
                SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 10}}
            app = create_app(Config)
            app.instance_path = folder
            with app.app_context():
                reset_cache()
                db.create_all()
                rbac_service.ensure_seeded()
                db.session.add_all(User(name=name, username=name, email=name + "@example.invalid", role="admin")
                                   for name in ("adminA", "adminB"))
                db.session.commit()
                ids = [user.id for user in User.query.order_by(User.id).all()]
            for decisions in ((True, True), (True, False), (False, False)):
                with app.app_context():
                    category = Category(name=str(decisions), approval_status="pending", is_active=False)
                    db.session.add(category)
                    db.session.commit()
                    category_id = category.id
                barrier = Barrier(2)
                get = category_service.get_category_or_404
                def stale_read(key):
                    record = get(key)
                    barrier.wait(timeout=10)
                    return record
                def decide(pair):
                    user_id, approve = pair
                    with app.test_request_context():
                        login_user(db.session.get(User, user_id))
                        try:
                            category_service.decide_category(category_id, approve=approve, reason="Decisión concurrente")
                            return 200, user_id, approve
                        except ConflictError:
                            return 409, user_id, approve
                with patch.object(category_service, "get_category_or_404", side_effect=stale_read):
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        results = list(pool.map(decide, zip(ids, decisions)))
                self.assertEqual(sorted(status for status, _, _ in results), [200, 409])
                winner = next(item for item in results if item[0] == 200)
                with app.app_context():
                    final = db.session.get(Category, category_id)
                    self.assertEqual(final.approved_by_user_id, winner[1])
                    self.assertEqual(final.is_active, winner[2])
                    self.assertEqual(final.approval_status, "approved" if winner[2] else "rejected")
            with app.app_context():
                db.session.remove()
                db.engine.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)
