"""MariaDB/MySQL exclusivamente desechable: conserva las guardas de la suite del tutor."""
import importlib.util
from pathlib import Path
import unittest
from sqlalchemy import create_engine, inspect, text
from alembic.migration import MigrationContext
from alembic.operations import Operations
import test_purchase_tutor_concurrency as base
from app.models.purchase import ProductSupplier
from app.models.purchase_correction import PurchaseReceptionCorrection
from app.services import product_supplier_service, inventory_service, purchase_correction_service, purchase_service


class PurchaseIntegrityConcurrencyTests(base.ConcurrencyTests):
    def correction(self, order, quantity):
        receipt_id = order["receptions"][0]["id"]
        row = self.ok(201, self.call("inventario", "post", f"/api/purchases/receptions/{receipt_id}/corrections", {
            "reason": "Corrección de conteo contrastada con el proveedor",
            "items": [{"line_id": order["items"][0]["id"], "quantity": quantity}]}))
        return f"/api/purchases/receptions/{receipt_id}/corrections/{row['id']}/approve"

    def test_association_edit_and_approval_use_the_same_lock_order(self):
        product_id = self.product("INT-LINK")
        order = self.draft(product_id)
        self.fresh()
        link_id = ProductSupplier.query.filter_by(product_id=product_id, supplier_id=self.supplier.id).one().id
        edit = ("edit", self.later("asistente", "patch", f"/api/products/{product_id}/suppliers/{link_id}", {
            "purchase_price": "8.00", "purchase_price_currency": "VES"}))
        approve = ("approve", self.later("admin", "post", f"/api/purchases/{order['id']}/approve"))
        with base.hold(product_supplier_service, "_product", thread="edit") as entered:
            results = self.sequenced_without_deadlock(edit, approve, entered)
        self.assertEqual((results["edit"][0], results["approve"][0]), (200, 200), results)

    def test_two_correction_approvals_create_one_compensation(self):
        product_id = self.product("INT-CORRECTION")
        order = self.closed_partial(product_id)
        path = self.correction(order, 7)
        with base.hold(inventory_service, "register_entry", thread="first") as entered:
            results = self.sequenced(("first", self.later("admin", "post", path)),
                                      ("second", self.later("admin", "post", path)), entered)
        self.assertEqual((results["first"][0], results["second"][0]), (200, 409), results)
        self.fresh()
        self.assertEqual(base.db.session.get(base.Product, product_id).current_stock, 17)
        rows = PurchaseReceptionCorrection.query.join(base.PurchaseReception).filter(base.PurchaseReception.purchase_order_id == order["id"]).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "approved")

    def test_movement_first_invalidates_pending_correction(self):
        product_id = self.product("INT-MOVEMENT")
        order = self.closed_partial(product_id)
        path = self.correction(order, 7)
        movement = ("movement", self.later("inventario", "post", "/api/inventory/exit", {
            "product_id": product_id, "quantity": 1, "reason": "Salida posterior"}))
        approve = ("approve", self.later("admin", "post", path))
        with base.hold(inventory_service, "_create_movement", thread="movement") as entered:
            results = self.sequenced(movement, approve, entered)
        self.assertEqual((results["movement"][0], results["approve"][0]), (201, 409), results)
        self.fresh()
        self.assertEqual(base.db.session.get(base.Product, product_id).current_stock, 15)

    def test_pending_correction_blocks_concurrent_shortage(self):
        product_id = self.product("INT-SHORTAGE")
        order = self.closed_partial(product_id)
        receipt_id = order["receptions"][0]["id"]
        propose = ("correction", self.later("inventario", "post", f"/api/purchases/receptions/{receipt_id}/corrections", {
            "reason": "Se está revisando el conteo recibido", "items": [{"line_id": order["items"][0]["id"], "quantity": 7}]}))
        shortage = ("shortage", self.later("inventario", "post", f"/api/replenishment-requests/from-order/{order['id']}"))
        with base.hold(purchase_correction_service, "lock_products", thread="correction") as entered:
            results = self.sequenced(propose, shortage, entered)
        self.assertEqual((results["correction"][0], results["shortage"][0]), (201, 409), results)


class PurchaseMigrationTests(unittest.TestCase):
    def test_additive_migration_preserves_existing_data_without_inventing_a_snapshot(self):
        database = "conejo_tutor_test_" + f"{int(base.DATABASE[-12:], 16) ^ 1:012x}"
        server = create_engine(base.SERVER)
        with server.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {database}"))
            mysql8 = "mariadb" not in connection.execute(text("SELECT VERSION()")).scalar_one().lower()
            collation = "utf8mb4_0900_ai_ci" if mysql8 else "utf8mb4_unicode_ci"
            connection.execute(text(f"CREATE DATABASE {database} CHARACTER SET utf8mb4 COLLATE {collation}"))
        server.dispose()
        class Config(base.MariaConfig):
            SQLALCHEMY_DATABASE_URI = base.SERVER.set(database=database).render_as_string(hide_password=False)
        app = base.create_app(Config)
        with app.app_context():
            base.db.create_all()
            category = base.Category(name="Migración desechable")
            user = base.User(name="Auditor", username="migration", email="migration@example.invalid", role="admin")
            supplier = base.Supplier(name="Proveedor histórico", tax_id="TEST-MIGRATION")
            base.db.session.add_all([category,user,supplier]);base.db.session.flush()
            product = base.Product(code="MIG-01", name="Producto previo", category_id=category.id, current_stock=3,
                minimum_stock=0, purchase_price=2, sale_price=3, purchase_price_currency="USD", sale_price_currency="USD")
            base.db.session.add(product);base.db.session.flush()
            movement = base.StockMovement(product_id=product.id, movement_type="entrada", quantity=3, previous_stock=0, new_stock=3)
            order = base.PurchaseOrder(supplier_id=supplier.id, created_by_user_id=user.id, status="completed", currency="USD", total=6, subtotal=6)
            base.db.session.add_all([movement,order]);base.db.session.commit()
            engine = base.db.engine
            base.db.session.remove()
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE purchase_reception_correction_lines"))
                connection.execute(text("DROP TABLE purchase_reception_corrections"))
                connection.execute(text("ALTER TABLE purchase_orders DROP COLUMN supplier_snapshot"))
                connection.execute(text("ALTER TABLE supplier_changes DROP COLUMN reason"))
                spec = importlib.util.spec_from_file_location("purchase_migration", Path(__file__).parents[1]/"migrations/versions/0012_purchase_integrity.py")
                migration = importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
                self.assertIn("supplier_snapshot", {c["name"] for c in inspect(connection).get_columns("purchase_orders")})
                self.assertEqual(connection.execute(text("SELECT current_stock FROM products WHERE code='MIG-01'")).scalar_one(), 3)
                self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM stock_movements")).scalar_one(), 1)
                self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM purchase_orders WHERE supplier_snapshot IS NULL")).scalar_one(), 1)
                self.assertTrue(inspect(connection).has_table("purchase_reception_corrections"))
                with self.assertRaises(RuntimeError):
                    migration.downgrade()
            engine.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2, failfast=True)
