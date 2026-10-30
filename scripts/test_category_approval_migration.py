"""Migración repetible sobre un esquema anterior SQLite, sin configuración ni red."""
import unittest
from sqlalchemy import create_engine, inspect, text
from migrate_category_approval import apply_changes


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as connection:
            for ddl in (
                "CREATE TABLE categories(id INTEGER PRIMARY KEY, name VARCHAR(100), is_active BOOLEAN)",
                "CREATE TABLE users(id INTEGER PRIMARY KEY)",
                "CREATE TABLE roles(id INTEGER PRIMARY KEY, slug VARCHAR(20))",
                "CREATE TABLE permissions(id INTEGER PRIMARY KEY, code VARCHAR(64), module VARCHAR(40), label VARCHAR(120))",
                "CREATE TABLE role_permissions(role_id INTEGER, permission_id INTEGER, PRIMARY KEY(role_id,permission_id))",
            ):
                connection.execute(text(ddl))
            connection.execute(text("INSERT INTO roles VALUES(1,'admin'),(2,'asistente'),(3,'inventario')"))
            connection.execute(text("INSERT INTO permissions VALUES(1,'categories:write','categories','Crear y editar')"))
            connection.execute(text("INSERT INTO role_permissions VALUES(1,1),(3,1)"))
            for i in range(10):
                connection.execute(text("INSERT INTO categories VALUES(:i,:n,:a)"),
                                   {"i": i+1, "n": f"Categoría {i}", "a": i != 0})

    def test_backfill_preserves_ten_names_ids_and_inactive_state_and_replays(self):
        with self.engine.begin() as connection:
            before = connection.execute(text("SELECT id,name,is_active FROM categories ORDER BY id")).all()
            apply_changes(connection)
            self.assertEqual(connection.execute(text("SELECT id,name,is_active FROM categories ORDER BY id")).all(), before)
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM categories WHERE approval_status='approved'")).scalar_one(), 10)
            connection.execute(text("UPDATE categories SET approval_status='pending',is_active=0 WHERE id=1"))
            apply_changes(connection)
            self.assertEqual(connection.execute(text("SELECT approval_status FROM categories WHERE id=1")).scalar_one(), "pending")
            grants = connection.execute(text("SELECT r.slug,p.code FROM role_permissions rp JOIN roles r "
                                             "ON r.id=rp.role_id JOIN permissions p ON p.id=rp.permission_id")).all()
            self.assertIn(("asistente", "categories:propose"), grants)
            self.assertIn(("admin", "categories:approve"), grants)
            self.assertFalse(any(role == "inventario" for role, _ in grants))

    def test_collision_fails_before_ddl_without_losing_records(self):
        with self.engine.begin() as connection:
            connection.execute(text("INSERT INTO categories VALUES(11,' CATEGORÍA  1 ',1)"))
            with self.assertRaises(RuntimeError):
                apply_changes(connection)
            self.assertNotIn("approval_status", {c["name"] for c in inspect(connection).get_columns("categories")})
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM categories")).scalar_one(), 11)


if __name__ == "__main__":
    unittest.main(verbosity=2)
