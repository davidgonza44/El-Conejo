"""Invariantes de modificación de categorías en SQLite desechable, sin MySQL."""
import unittest
from unittest.mock import patch

from approval_test_support import IsolatedCase
from app.extensions import db
from app.models import Category, CategoryChange, StockMovement
from app.services import category_change_service, dashboard_service, rbac_service


class CategoryChangesTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.category_id = self.product.category_id
        self.path = f"/api/categories/{self.category_id}"

    def snapshot(self):
        db.session.expire_all()
        return db.session.get(Category, self.category_id).to_dict()

    def propose(self, changes, method="patch"):
        response = getattr(self.clients["asistente"], method)(self.path, json=changes)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["approval_status"], "approved")
        self.assertEqual(response.json["pending_change"]["status"], "pending")
        return response.json["pending_change"]

    def decide(self, proposal, action="approve", actor="admin"):
        return self.clients[actor].post(self.path + "/" + action,
            json={"proposal_id": proposal["id"], "reason": "No autorizado"})

    def test_admin_and_inventory_cannot_edit_any_field_or_state(self):
        before = self.snapshot()
        for actor in ("admin", "inventario"):
            for method in ("patch", "put"):
                for data in ({"name": "Directo"}, {"description": "Directo"}, {"color": "red"},
                             {"icon": "hammer"}, {"is_active": False}, {"status": "approved"}):
                    response = getattr(self.clients[actor], method)(self.path, json=data)
                    self.assertEqual(response.status_code, 403, response.json)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(CategoryChange.query.count(), 0)
        self.assertEqual(self.clients["admin"].delete(self.path).status_code, 403)
        self.assertEqual(self.clients["asistente"].delete(self.path).status_code, 403)

    def test_service_denies_admin_even_with_propose_permission(self):
        from flask_login import login_user
        from app.models.role import Permission, Role
        from app.services import category_service
        from app.services.exceptions import ValidationError
        role = Role.query.filter_by(slug="admin").one()
        role.permissions.append(Permission.query.filter_by(code="categories:propose").one())
        db.session.commit()
        rbac_service.invalidate_cache()
        with self.app.test_request_context():
            login_user(self.users["admin"])
            for function in (category_service.update_category, category_change_service.propose_change):
                with self.assertRaises(ValidationError) as error:
                    function(self.category_id, {"is_active": False})
                self.assertEqual(error.exception.status_code, 403)

    def test_assistant_proposes_all_fields_without_mutating_operative_record(self):
        before = self.snapshot()
        changes = dict(name="Nombre propuesto", description="Descripción propuesta", color="red", icon="hammer", is_active=False)
        proposal = self.propose(changes)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(proposal["changes"], changes)
        self.assertEqual(proposal["previous_values"], {key: before[key] for key in changes})
        self.assertEqual(proposal["proposed_by_user_id"], self.users["asistente"].id)
        self.assertEqual(self.stock(), 10)
        self.assertEqual(StockMovement.query.count(), 0)

    def test_approval_applies_once_and_preserves_actor_trace(self):
        before = self.snapshot()
        changes = dict(name="Aprobada nueva", description="Nueva descripción", color="green", icon="zap", is_active=False)
        proposal = self.propose(changes, "put")
        self.assertEqual(self.decide(proposal).status_code, 200)
        expected = before | changes
        self.assertEqual(self.snapshot(), expected)
        final = self.clients["admin"].get(self.path).json
        self.assertIsNone(final["pending_change"])
        self.assertEqual(final["last_change"]["status"], "approved")
        self.assertEqual(final["last_change"]["reviewed_by_user_id"], self.users["admin"].id)
        self.assertEqual(final["last_change"]["proposed_by_user_id"], self.users["asistente"].id)
        self.assertEqual(final["last_change"]["proposed_by_name"], self.users["asistente"].name)
        self.assertEqual(final["last_change"]["reviewed_by_name"], self.users["admin"].name)
        self.assertIsNotNone(final["last_change"]["reviewed_at"])
        for action in ("approve", "reject"):
            self.assertEqual(self.decide(proposal, action, "admin2").status_code, 409)
            self.assertEqual(self.snapshot(), expected)
        self.assertEqual(CategoryChange.query.count(), 1)

    def test_rejection_preserves_every_original_value_and_retries_conflict(self):
        before = self.snapshot()
        proposal = self.propose(dict(name="No aplicar", description="No aplicar", color="red", icon="hammer", is_active=False))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.decide(proposal, "reject").status_code, 200)
        self.assertEqual(self.snapshot(), before)
        rejected = self.clients["admin"].get(self.path).json["last_change"]
        self.assertEqual((rejected["status"], rejected["rejection_reason"]), ("rejected", "No autorizado"))
        for action in ("approve", "reject"):
            self.assertEqual(self.decide(proposal, action).status_code, 409)
        self.assertEqual(self.snapshot(), before)
        self.propose({"name": "Otra propuesta"})

    def test_pending_change_is_immutable_and_admin_cannot_alter_it(self):
        before = self.snapshot()
        proposal = self.propose({"name": "Primera"})
        self.assertEqual(self.clients["asistente"].put(self.path, json={"name": "Segunda"}).status_code, 409)
        self.assertEqual(self.clients["admin"].patch(self.path, json={"name": "Alterada"}).status_code, 403)
        self.assertEqual(CategoryChange.query.one().changes, proposal["changes"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.clients["admin"].post(self.path + "/approve", json={}).status_code, 409)
        self.assertEqual(self.clients["admin"].post(self.path + "/approve", json={"proposal_id": str(proposal["id"])}).status_code, 400)

    def test_proposer_cannot_decide_after_becoming_admin(self):
        proposal = self.propose({"name": "Autoaprobación"})
        self.users["asistente"].role = "admin"
        db.session.commit()
        rbac_service.invalidate_cache()
        for action in ("approve", "reject"):
            self.assertEqual(self.decide(proposal, action, "asistente").status_code, 403)
        self.assertEqual(CategoryChange.query.one().status, "pending")

    def test_inventory_and_assistant_cannot_decide(self):
        proposal = self.propose({"name": "Cambio"})
        for actor in ("inventario", "asistente"):
            for action in ("approve", "reject"):
                self.assertEqual(self.decide(proposal, action, actor).status_code, 403)

    def test_pending_filter_dashboard_and_active_use_include_change(self):
        self.propose({"name": "Cambio"})
        pending = self.clients["admin"].get("/api/categories?approval_status=pending").json
        self.assertEqual([row["id"] for row in pending["items"]], [self.category_id])
        self.assertTrue(pending["items"][0]["has_pending_change"])
        self.assertEqual(self.clients["admin"].get("/api/categories?approval_status=approved").json["items"], [])
        active = self.clients["inventario"].get("/api/categories?status=active").json["items"]
        self.assertEqual(active[0]["name"], "Pruebas aisladas")
        from flask_login import login_user
        with self.app.test_request_context():
            login_user(self.users["admin"])
            items = dashboard_service.pending_proposals("admin")
        self.assertEqual(next(item["count"] for item in items if item["label"] == "Categorías pendientes"), 1)

    def test_admin_and_inventory_html_has_no_editor_or_save_action(self):
        for actor in ("admin", "inventario"):
            page = self.clients[actor].get("/categories").text
            self.assertNotIn('id="cl-name"', page)
            self.assertNotIn('data-cl-action="edit"', page)
            self.assertNotIn('data-cl-action="save"', page)
            self.assertNotIn('data-cl-action="status"', page)
            self.assertNotIn('Guardar cambios', page)
        self.assertIn('id="cl-name"', self.clients["asistente"].get("/categories").text)

    def test_revalidation_failure_is_atomic_and_keeps_proposal_pending(self):
        before = self.snapshot()
        proposal = self.propose({"name": "Nombre disponible", "color": "red"})
        db.session.add(Category(name="Nombre disponible"))
        db.session.commit()
        self.assertEqual(self.decide(proposal).status_code, 409)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(CategoryChange.query.one().status, "pending")

    def test_stale_master_is_not_overwritten(self):
        proposal = self.propose({"name": "Propuesto"})
        db.session.get(Category, self.category_id).name = "Cambio externo"
        db.session.commit()
        self.assertEqual(self.decide(proposal).status_code, 409)
        self.assertEqual(self.snapshot()["name"], "Cambio externo")

    def test_invalid_fields_and_noop_create_no_proposal(self):
        for data in ({}, {"status": "approved"}, {"approval_status": "pending"}, {"is_active": "false"}, {"name": "Pruebas aisladas"}):
            self.assertEqual(self.clients["asistente"].patch(self.path, json=data).status_code, 400)
        self.assertEqual(CategoryChange.query.count(), 0)

    def test_migration_is_additive_idempotent_and_preserves_categories(self):
        from migrate_category_changes import apply_changes
        from sqlalchemy import inspect
        before = self.snapshot()
        with db.engine.begin() as connection:
            CategoryChange.__table__.drop(connection)
            apply_changes(connection)
            apply_changes(connection)
            self.assertEqual(len(inspect(connection).get_foreign_keys("category_changes")), 3)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(CategoryChange.query.count(), 0)


class ConcurrentCategoryChangeTests(unittest.TestCase):
    def test_independent_sessions_only_one_decision_applies(self):
        import tempfile
        from pathlib import Path
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from flask_login import login_user
        from approval_test_support import IsolatedConfig
        from app import create_app
        from app.models import User
        from app.services.exceptions import ConflictError
        from app.utils.schema import reset_cache

        with tempfile.TemporaryDirectory(prefix="category-change-concurrency-") as folder:
            class Config(IsolatedConfig):
                SQLALCHEMY_DATABASE_URI = "sqlite:///" + (Path(folder) / "test.sqlite").as_posix()
                SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 10}}
            app = create_app(Config)
            app.instance_path = folder
            with app.app_context():
                reset_cache()
                db.create_all()
                rbac_service.ensure_seeded()
                db.session.add_all(User(name=name, username=name, email=name + "@example.invalid", role=role)
                    for name, role in (("adminA", "admin"), ("adminB", "admin"), ("proposer", "asistente")))
                db.session.commit()
                ids = {user.username: user.id for user in User.query.all()}
            try:
                for decisions in ((True, True), (True, False), (False, False)):
                    with app.app_context():
                        category = Category(name=str(decisions))
                        db.session.add(category)
                        db.session.flush()
                        proposal = CategoryChange(category_id=category.id, proposed_by_user_id=ids["proposer"],
                            changes={"name": str(decisions) + " cambiado"}, previous_values={"name": category.name})
                        db.session.add(proposal)
                        db.session.commit()
                        category_id, proposal_id, original = category.id, proposal.id, category.name
                    barrier = Barrier(2)
                    locked = category_change_service._locked_category
                    def simultaneous_read(key):
                        barrier.wait(timeout=10)
                        return locked(key)
                    def decide(pair):
                        user_id, approve = pair
                        with app.test_request_context():
                            login_user(db.session.get(User, user_id))
                            try:
                                category_change_service.decide_change(category_id, proposal_id, approve=approve, reason="Rechazo concurrente")
                                return 200, user_id, approve
                            except ConflictError:
                                return 409, user_id, approve
                    with patch.object(category_change_service, "_locked_category", side_effect=simultaneous_read):
                        with ThreadPoolExecutor(max_workers=2) as pool:
                            results = list(pool.map(decide, zip((ids["adminA"], ids["adminB"]), decisions)))
                    self.assertEqual(sorted(row[0] for row in results), [200, 409])
                    winner = next(row for row in results if row[0] == 200)
                    with app.app_context():
                        final = db.session.get(Category, category_id)
                        reviewed = db.session.get(CategoryChange, proposal_id)
                        self.assertEqual(final.name, original + " cambiado" if winner[2] else original)
                        self.assertEqual(reviewed.reviewed_by_user_id, winner[1])
                        self.assertEqual(reviewed.status, "approved" if winner[2] else "rejected")
            finally:
                with app.app_context():
                    db.session.remove()
                    db.engine.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)
