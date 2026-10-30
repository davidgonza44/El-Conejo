"""Fresh provisioning/idempotency tests in memory. No real database connection."""
import os
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

import unittest
from flask import Flask
from sqlalchemy import text

from app.extensions import db
from app.models import AuthIdentity, Permission, Role, User
from app.models.role import role_permissions
from app.services import auth_service, rbac_service
from docker_support.provision_demo_accounts import provision


class DemoAccounts(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.config.update(SQLALCHEMY_DATABASE_URI="sqlite:///:memory:", TESTING=True)
        db.init_app(app)
        self.context = app.app_context()
        self.context.push()
        tables = [User.__table__, Role.__table__, Permission.__table__, role_permissions, AuthIdentity.__table__]
        db.metadata.create_all(db.engine, tables=tables)
        db.session.execute(text("CREATE TABLE container_installation (id INT, admin_id INT)"))
        rbac_service.ensure_seeded()
        self.payload = dict(admin_username="admin_demo_test", admin_password="Aa1!SyntheticAdmin",
                            passwords={role: "Aa1!Synthetic" + role for role in ("inventario", "asistente", "vendedor", "soporte_ti")})
        admin = auth_service.register(dict(name="DEMO Admin", username=self.payload["admin_username"],
            email="admin@example.invalid", role="admin", password=self.payload["admin_password"]), allowed_roles=("admin",))
        buyer = auth_service.register(dict(name="DEMO Compras", username="demo_compras", email="buyer@example.invalid",
            role="inventario", password="Aa1!SeedOnly"), allowed_roles=("inventario",))
        self.buyer_id, self.admin_hash = buyer.id, admin.password_hash
        db.session.execute(text("INSERT INTO container_installation VALUES (1,:id)"), {"id":admin.id})
        db.session.commit()
        self.permissions = {r.slug:r.permission_codes for r in Role.query.all()}

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()

    def test_fresh_and_retry_preserve_accounts_permissions_and_passwords(self):
        provision(self.payload)
        self.assertEqual(User.query.count(), 5)
        self.assertEqual(User.query.filter_by(username="demo_inventario").one().id, self.buyer_id)
        self.assertEqual(User.query.filter_by(role="admin").one().password_hash, self.admin_hash)
        self.assertEqual(self.permissions, {r.slug:r.permission_codes for r in Role.query.all()})
        hashes = {u.id:u.password_hash for u in User.query.all()}
        provision(self.payload)
        self.assertEqual(hashes, {u.id:u.password_hash for u in User.query.all()})
        for role, password in self.payload["passwords"].items():
            self.assertTrue(User.query.filter_by(role=role).one().check_password(password))

    def test_collision_does_not_rotate_existing_passwords(self):
        provision(self.payload)
        hashes = {u.id:u.password_hash for u in User.query.all()}
        self.payload["passwords"]["vendedor"] = "Aa1!UnexpectedRotation"
        with self.assertRaises(RuntimeError):
            provision(self.payload)
        self.assertEqual(hashes, {u.id:u.password_hash for u in User.query.all()})

    def test_new_seed_seller_is_provisioned_without_an_extra_account(self):
        seller = auth_service.register(dict(name="DEMO Ventas", username="demo_ventas",
            email="demo_ventas@example.invalid", role="vendedor", password="Aa1!SyntheticSeed"), allowed_roles=("vendedor",))
        seller_id = seller.id
        provision(self.payload)
        self.assertEqual(User.query.count(), 5)
        self.assertEqual(User.query.filter_by(username="demo_vendedor").one().id, seller_id)


if __name__ == "__main__":
    unittest.main()
