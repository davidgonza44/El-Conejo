"""Disposable SQLite and mocked target guards; never connect to a business database."""
from contextlib import ExitStack
from io import StringIO
import os
from pathlib import Path
import secrets
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import BigInteger, event, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.compiler import compiles

import create_tesis_admin as admin


@compiles(BigInteger, "sqlite")
def sqlite_bigint(_type, _compiler, **_kwargs):
    return "INTEGER"


class TransactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        cls.stack.enter_context(patch("pymysql.connect", side_effect=AssertionError("Live database forbidden")))
        cls.domain = admin.load_auth()
        cls.root = Path(cls.stack.enter_context(tempfile.TemporaryDirectory(prefix="conejo-admin-tests-")))
        cls.app = admin.build_app("sqlite:///" + (cls.root / "isolated.sqlite").as_posix(), cls.domain)
        cls.context = cls.app.app_context()
        cls.context.push()
        cls.stack.callback(cls.context.pop)
        cls.engine = cls.domain.db.engine
        cls.stack.callback(cls.engine.dispose)
        cls.stack.callback(cls.domain.db.session.remove)

        @event.listens_for(cls.engine, "connect")
        def sqlite_connect(dbapi, _record):
            dbapi.isolation_level = None
            dbapi.execute("PRAGMA foreign_keys=ON")

        @event.listens_for(cls.engine, "begin")
        def sqlite_begin(connection):
            connection.exec_driver_sql("BEGIN")

        cls.domain.db.create_all()
        cls.domain.rbac_service.ensure_seeded()  # Disposable test setup only, never CLI behavior.
        cls.domain.db.session.remove()
        cls.tables = cls.domain.db.metadata.tables

    def setUp(self):
        self.scope = ExitStack()
        self.addCleanup(self.scope.close)
        self.connection = self.scope.enter_context(self.engine.connect())
        outer = self.connection.begin()
        self.scope.callback(outer.rollback)
        nested = self.connection.begin_nested()
        self.scope.callback(nested.rollback)
        self.scope.enter_context(admin.bound_connection(self.domain, self.connection))
        self.password = secrets.token_urlsafe(24) + "Aa1!"

    def create(self):
        return admin.create_account(self.domain, self.connection, self.tables, self.password)

    def test_real_registration_exact_delta_and_readonly_verification(self):
        before = admin.fingerprint(self.connection, self.tables)
        with patch.object(self.domain.auth_service, "login", side_effect=AssertionError("Login writes forbidden")):
            ids = self.create()
            verified = admin.verify_user(self.domain, self.password)
        self.assertEqual(verified, (ids["users"], ids["auth_identities"]))
        self.assertEqual(admin.fingerprint(self.connection, self.tables, ids), before)
        user = self.domain.models.User.query.filter_by(username=admin.USERNAME).one()
        self.assertNotEqual(user.password_hash, self.password)
        self.assertTrue(user.is_active)
        self.assertEqual(user.name, "Administrador")
        self.assertTrue(self.connection.in_nested_transaction())
        self.assertEqual(self.domain.models.PasswordHistory.query.count(), 0)
        self.assertEqual(self.domain.models.UserSession.query.count(), 0)
        self.assertEqual(self.domain.models.LoginEvent.query.count(), 0)

    def test_existing_user_aborts_without_update(self):
        self.create()
        before = admin.fingerprint(self.connection, self.tables)
        self.password = secrets.token_urlsafe(24) + "Bb2!"
        with self.assertRaisesRegex(admin.AdminError, "ya existe"):
            self.create()
        self.assertEqual(admin.fingerprint(self.connection, self.tables), before)

    def test_missing_or_inactive_role_never_seeds(self):
        with patch.object(self.domain.rbac_service, "ensure_seeded", side_effect=AssertionError("Role seeding forbidden")):
            role = self.domain.models.Role.query.filter_by(slug="admin").one()
            role.is_active = False
            self.domain.db.session.flush()
            before = admin.fingerprint(self.connection, self.tables)
            with self.assertRaisesRegex(admin.AdminError, "rol admin activo"):
                self.create()
            self.assertEqual(admin.fingerprint(self.connection, self.tables), before)
            role.is_active = True
            role.slug = "renamed"
            self.domain.db.session.flush()
            with self.assertRaisesRegex(admin.AdminError, "rol admin activo"):
                self.create()

    def test_incomplete_role_never_grants_extra_permissions(self):
        role = self.domain.models.Role.query.filter_by(slug="admin").one()
        role.permissions.pop()
        self.domain.db.session.flush()
        before = admin.fingerprint(self.connection, self.tables)
        with self.assertRaisesRegex(admin.AdminError, "todos los permisos"):
            self.create()
        self.assertEqual(admin.fingerprint(self.connection, self.tables), before)

    def test_failure_after_register_commit_is_still_rollbackable(self):
        before = admin.fingerprint(self.connection, self.tables)
        nested = self.connection.begin_nested()
        try:
            self.create()
            self.domain.db.session.remove()
            nested.rollback()
            self.assertEqual(admin.fingerprint(self.connection, self.tables), before)
        finally:
            if nested.is_active:
                nested.rollback()

    def test_unexpected_write_is_rejected_and_user_can_be_rolled_back(self):
        original = self.domain.auth_service.register
        before = admin.fingerprint(self.connection, self.tables)
        nested = self.connection.begin_nested()
        def unexpected(*args, **kwargs):
            user = original(*args, **kwargs)
            self.domain.db.session.execute(text("UPDATE roles SET name='forbidden' WHERE slug='admin'"))
            return user
        try:
            with patch.object(self.domain.auth_service, "register", side_effect=unexpected):
                with self.assertRaisesRegex(admin.AdminError, "fuera de la cuenta"):
                    self.create()
            self.domain.db.session.remove()
            nested.rollback()
            self.assertEqual(admin.fingerprint(self.connection, self.tables), before)
        finally:
            if nested.is_active:
                nested.rollback()


class GuardTests(unittest.TestCase):
    def test_missing_secret_is_removed_before_any_connection(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, "argv", ["create_tesis_admin.py"]), \
                patch.object(admin, "run", side_effect=AssertionError("Must not connect")), \
                patch("sys.stderr", new_callable=StringIO) as output:
            self.assertEqual(admin.main(), 1)
            self.assertIn("Falta TESIS_ADMIN_PASSWORD", output.getvalue())
            self.assertNotIn("TESIS_ADMIN_PASSWORD", os.environ)

    def test_secret_is_consumed_and_errors_do_not_print_sensitive_details(self):
        secret = secrets.token_urlsafe(24)
        def failure(password):
            self.assertNotIn("TESIS_ADMIN_PASSWORD", os.environ)
            self.assertEqual(password, secret)
            raise RuntimeError("SQL parameters " + password)
        with patch.dict(os.environ, {"TESIS_ADMIN_PASSWORD": secret}), patch.object(sys, "argv", ["script"]), \
                patch.object(admin, "run", side_effect=failure), patch("sys.stderr", new_callable=StringIO) as output:
            self.assertEqual(admin.main(), 1)
            self.assertNotIn(secret, output.getvalue())
            self.assertNotIn("SQL parameters", output.getvalue())

    def test_arguments_are_rejected_without_echoing_them(self):
        secret = secrets.token_urlsafe(24)
        with patch.dict(os.environ, {"TESIS_ADMIN_PASSWORD": secret}), patch.object(sys, "argv", ["script", secret]), \
                patch.object(admin, "run", side_effect=AssertionError("Must not connect")), \
                patch("sys.stderr", new_callable=StringIO) as output:
            self.assertEqual(admin.main(), 1)
            self.assertNotIn(secret, output.getvalue())

    def test_wrong_environment_aborts_before_auth_imports_or_network(self):
        for host, database in (("localhost", "ferreteria_conejo"), ("remote", admin.safety.DATABASE),
                               ("127.0.0.1", admin.safety.DATABASE)):
            with self.subTest(host=host, database=database), \
                    patch.dict(os.environ, {"DB_HOST": host, "DB_NAME": database}, clear=True), \
                    patch.object(admin, "load_auth", side_effect=AssertionError("Must not initialize")):
                with self.assertRaises(admin.safety.SeedError):
                    admin.run(secrets.token_urlsafe(24))

    def test_parsed_url_guards(self):
        allowed = URL.create("mysql+pymysql", username="root", host="127.0.0.1", port=3306,
                             database=admin.safety.DATABASE, query={"charset": "utf8mb4"})
        admin.validate_url(allowed)
        for value in (allowed.set(host="remote"), allowed.set(database="ferreteria_conejo"),
                      allowed.set(port=3307), allowed.set(query={"local_infile": "1"})):
            with self.assertRaises(admin.AdminError):
                admin.validate_url(value)

    def test_effective_database_checked_before_schema_or_auth_reads(self):
        connection = Mock(dialect=SimpleNamespace(name="mysql"))
        connection.execute.return_value.scalar.return_value = "ferreteria_conejo"
        with self.assertRaisesRegex(admin.AdminError, "base seleccionada"):
            admin.preflight(connection)
        self.assertEqual(connection.execute.call_count, 1)
        self.assertEqual(str(connection.execute.call_args.args[0]), "SELECT DATABASE()")


if __name__ == "__main__":
    unittest.main(verbosity=2)
