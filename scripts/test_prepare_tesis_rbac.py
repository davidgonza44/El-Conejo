"""Exercise the real RBAC service on disposable SQLite; network access is forbidden."""
from contextlib import ExitStack
from io import StringIO
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import BigInteger, event, text
from sqlalchemy.ext.compiler import compiles

import prepare_tesis_rbac as prep


@compiles(BigInteger, "sqlite")
def sqlite_bigint(_type, _compiler, **_kwargs):
    return "INTEGER"


class TransactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        cls.stack.enter_context(patch("pymysql.connect", side_effect=AssertionError("Live database forbidden")))
        cls.domain = prep.admin.load_auth()
        root = Path(cls.stack.enter_context(tempfile.TemporaryDirectory(prefix="conejo-rbac-tests-")))
        cls.app = prep.admin.build_app("sqlite:///" + (root / "isolated.sqlite").as_posix(), cls.domain)
        context = cls.app.app_context()
        context.push()
        cls.stack.callback(context.pop)
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
        cls.tables = cls.domain.db.metadata.tables

    def setUp(self):
        self.scope = ExitStack()
        self.addCleanup(self.scope.close)
        self.connection = self.scope.enter_context(self.engine.connect())
        outer = self.connection.begin()
        self.scope.callback(outer.rollback)
        nested = self.connection.begin_nested()
        self.scope.callback(nested.rollback)
        self.scope.enter_context(prep.admin.bound_connection(self.domain, self.connection))
        self.verify = self.scope.enter_context(patch.object(prep, "verify_database"))

    def prepare(self):
        return prep.prepare_catalog(self.domain, self.connection, self.tables)

    def snapshot(self):
        return prep.fingerprint(self.connection, self.tables)

    def test_empty_catalog_creates_only_exact_standard_rows(self):
        before = self.snapshot()
        self.assertEqual(self.prepare(), {"roles": 3, "permissions": 33, "role_permissions": 53})
        permissions, roles = prep.catalog(self.domain)
        self.assertEqual(set(permissions), self.domain.ALL_PERMISSIONS)
        self.assertEqual(set(roles), set(self.domain.rbac_service.ROLES))
        created = {"roles": {role.id for role in roles.values()},
                   "permissions": {permission.id for permission in permissions.values()}}
        self.assertEqual(prep.fingerprint(self.connection, self.tables, created), before)
        self.assertEqual(self.domain.models.User.query.count(), 0)
        self.assertEqual(self.domain.models.AuthIdentity.query.count(), 0)
        self.verify.assert_called_once_with(self.connection)
        self.assertTrue(self.connection.in_nested_transaction())

    def test_repeat_is_zero_delta_and_preserves_operator_role_text(self):
        self.prepare()
        role = self.domain.models.Role.query.filter_by(slug="admin").one()
        role.name, role.description = "Administración local", "Descripción personalizada"
        self.domain.db.session.flush()
        before = self.snapshot()
        self.assertEqual(self.prepare(), {"roles": 0, "permissions": 0, "role_permissions": 0})
        self.assertEqual(self.snapshot(), before)

    def test_partial_catalog_creates_missing_permissions_and_roles_only(self):
        service = self.domain.rbac_service
        codes = service.ROLE_PERMISSIONS["vendedor"]
        permissions = [self.domain.models.Permission(code=code, module=code.split(":")[0],
                       label=service.permission_label(code)) for code in sorted(codes)]
        role = self.domain.models.Role(slug="vendedor", name="Vendedor", is_system=True,
                                       is_active=True, permissions=permissions)
        self.domain.db.session.add(role)
        self.domain.db.session.flush()
        before = self.snapshot()
        self.assertEqual(self.prepare(), {"roles": 2, "permissions": 26, "role_permissions": 46})
        self.assertEqual(self.snapshot()["users"], before["users"])
        self.assertIsNone(role.description)  # Existing optional text is never normalized.

    def test_existing_user_and_custom_role_and_permission_are_untouched(self):
        permission = self.domain.models.Permission(code="custom:read", module="custom", label="Local")
        role = self.domain.models.Role(slug="custom", name="Local", permissions=[permission],
                                       is_system=False, is_active=False)
        user = self.domain.models.User(name="Existing local fixture", username="existing_fixture",
                                       email="fixture@example.invalid", role="custom", is_active=False)
        self.domain.db.session.add_all([role, user])
        self.domain.db.session.flush()
        before = self.snapshot()
        self.assertEqual(self.prepare(), {"roles": 3, "permissions": 33, "role_permissions": 53})
        self.assertEqual(self.snapshot()["users"], before["users"])
        self.assertEqual(self.domain.models.User.query.count(), 1)
        self.assertFalse(role.is_active)
        self.assertEqual(role.permission_codes, {"custom:read"})

    def test_incompatible_existing_role_aborts_without_writes(self):
        self.prepare()
        role = self.domain.models.Role.query.filter_by(slug="admin").one()
        for field in ("is_active", "is_system", "permissions"):
            with self.subTest(field=field):
                original = getattr(role, field)
                setattr(role, field, [] if field == "permissions" else False)
                self.domain.db.session.flush()
                before = self.snapshot()
                with patch.object(self.domain.rbac_service, "ensure_seeded", side_effect=AssertionError("No writes")):
                    with self.assertRaisesRegex(prep.admin.AdminError, "rol estándar existente"):
                        self.prepare()
                self.assertEqual(self.snapshot(), before)
                setattr(role, field, original)
                self.domain.db.session.flush()

    def test_incompatible_permission_metadata_aborts_without_writes(self):
        code = sorted(self.domain.ALL_PERMISSIONS)[0]
        row = self.domain.models.Permission(code=code, module=code.split(":")[0],
                                            label=self.domain.rbac_service.permission_label(code))
        self.domain.db.session.add(row)
        for field in ("module", "label"):
            with self.subTest(field=field):
                original = getattr(row, field)
                setattr(row, field, "incompatible")
                self.domain.db.session.flush()
                before = self.snapshot()
                with self.assertRaisesRegex(prep.admin.AdminError, "metadatos incompatibles"):
                    self.prepare()
                self.assertEqual(self.snapshot(), before)
                setattr(row, field, original)

    def test_second_effective_database_check_precedes_the_writing_service(self):
        self.verify.side_effect = prep.admin.AdminError("Base efectiva incorrecta")
        before = self.snapshot()
        with patch.object(self.domain.rbac_service, "ensure_seeded", side_effect=AssertionError("No writes")):
            with self.assertRaisesRegex(prep.admin.AdminError, "Base efectiva"):
                self.prepare()
        self.assertEqual(self.snapshot(), before)

    def test_unexpected_write_and_extra_rbac_row_are_rollbackable_after_service_commit(self):
        original = self.domain.rbac_service.ensure_seeded
        for extra in (False, True):
            with self.subTest(extra_rbac_row=extra):
                before = self.snapshot()
                nested = self.connection.begin_nested()
                def unexpected():
                    original()
                    if extra:
                        row = self.domain.models.Permission(code="unexpected:read", module="unexpected", label="Test")
                        self.domain.db.session.add(row)
                        self.domain.db.session.flush()
                    else:
                        self.domain.db.session.execute(text("UPDATE users SET is_active=0"))
                try:
                    with patch.object(self.domain.rbac_service, "ensure_seeded", side_effect=unexpected):
                        with self.assertRaises(prep.admin.AdminError):
                            self.prepare()
                    self.domain.db.session.remove()
                    nested.rollback()
                    self.assertEqual(self.snapshot(), before)
                finally:
                    if nested.is_active:
                        nested.rollback()


class GuardTests(unittest.TestCase):
    ENV = {"DB_HOST": "localhost", "DB_PORT": "3306", "DB_NAME": "ferreteria_conejo_tesis",
           "DB_USER": "root", "DB_ALLOW_EMPTY_PASSWORD": "1"}

    def test_explicit_target_and_empty_password_are_required_before_import_or_network(self):
        invalid = ({"DB_HOST": "remote"}, {"DB_HOST": "127.0.0.1"}, {"DB_NAME": "ferreteria_conejo"},
                   {"DB_PORT": "3307"}, {"DB_USER": "other"}, {"DB_ALLOW_EMPTY_PASSWORD": "0"},
                   {"DATABASE_URL": "forbidden"}, {"SQLALCHEMY_DATABASE_URI": "forbidden"})
        for change in invalid:
            with self.subTest(change=change), patch.dict(os.environ, self.ENV | change, clear=True), \
                    patch.object(prep.admin, "load_auth", side_effect=AssertionError("No imports/network")):
                with self.assertRaises((prep.admin.AdminError, prep.admin.safety.SeedError)):
                    prep.run()

    def test_localhost_resolution_is_pinned_and_cannot_point_remote(self):
        with patch.dict(os.environ, self.ENV, clear=True), patch("socket.getaddrinfo") as lookup:
            lookup.return_value = [(2, 1, 6, "", ("127.0.0.1", 3306))]
            url = prep.configured_url()
            self.assertEqual((url.host, url.database, url.password), ("127.0.0.1", self.ENV["DB_NAME"], ""))
            lookup.return_value = [(2, 1, 6, "", ("192.0.2.1", 3306))]
            with self.assertRaises(prep.admin.safety.SeedError):
                prep.configured_url()

    def test_wrong_effective_database_aborts_before_other_queries(self):
        for check in (prep.admin.preflight, prep.verify_database):
            connection = Mock(dialect=SimpleNamespace(name="mysql"))
            connection.execute.return_value.scalar.return_value = "ferreteria_conejo"
            with self.assertRaises(prep.admin.AdminError):
                check(connection)
            self.assertEqual(connection.execute.call_count, 1)
            self.assertEqual(str(connection.execute.call_args.args[0]), "SELECT DATABASE()")

    def test_cli_errors_are_sanitized_and_arguments_rejected_before_connection(self):
        for arguments in (["script"], ["script", "private_argument"]):
            with self.subTest(arguments=len(arguments)), patch.object(sys, "argv", arguments), \
                    patch.object(prep, "run", side_effect=RuntimeError("private_details")) as run, \
                    patch("sys.stderr", new_callable=StringIO) as output:
                self.assertEqual(prep.main(), 1)
                self.assertNotIn("private_", output.getvalue())
                self.assertEqual(run.call_count, int(len(arguments) == 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
