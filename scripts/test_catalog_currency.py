"""Currency contracts in disposable SQLite only; no MySQL, user files or delivery."""
import csv
import importlib.util
from io import StringIO
from pathlib import Path
import unittest

from sqlalchemy import Column, Integer, MetaData, Numeric, Table, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from alembic.migration import MigrationContext
from alembic.operations import Operations

from approval_test_support import IsolatedCase
from app.extensions import db
from app.models import Product, ProductChange, StockMovement
from app.models.purchase import ProductSupplier
from app.utils.purchase_currency import format_amount


class CatalogCurrency(IsolatedCase):
    def creation(self, code="CURRENCY-NEW", sale="USD", cost="VES"):
        return {"code": code, "name": "Producto de prueba", "category_id": self.product.category_id,
                "sale_price": "9.25", "sale_price_currency": sale,
                "purchase_price": "3.50", "purchase_price_currency": cost,
                "current_stock": 0, "minimum_stock": 0}

    def test_new_prices_accept_independent_explicit_usd_and_ves(self):
        for sale, cost in (("USD", "VES"), ("VES", "USD")):
            body = self.creation("NEW-" + sale, sale, cost)
            result = self.clients["asistente"].post("/api/products", json=body)
            self.assertEqual(result.status_code, 201, result.json)
            approved = self.clients["admin"].post(f"/api/products/{result.json['id']}/approve")
            self.assertEqual(approved.status_code, 200, approved.json)
            self.assertEqual((approved.json["sale_price_currency"], approved.json["purchase_price_currency"]), (sale, cost))
            self.assertEqual((approved.json["sale_price"], approved.json["purchase_price"]), (9.25, 3.5))
        self.assertEqual(StockMovement.query.count(), 0)

    def test_required_currencies_reject_null_invalid_and_variants(self):
        invalid = (None, "", "EUR", "usd", "ves", "USD ", " VES", "USD\n", 1, True, [], {})
        for field in ("sale_price_currency", "purchase_price_currency"):
            for value in invalid:
                with self.subTest(field=field, value=value):
                    body = self.creation()
                    body[field] = value
                    self.assertEqual(self.clients["asistente"].post("/api/products", json=body).status_code, 400)
            body = self.creation()
            del body[field]
            self.assertEqual(self.clients["asistente"].post("/api/products", json=body).status_code, 400)
        self.assertEqual(Product.query.count(), 1)
        self.assertEqual(StockMovement.query.count(), 0)

    def test_price_and_currency_are_approved_together_and_description_preserves_them(self):
        path = f"/api/products/{self.product.id}"
        proposed = self.clients["asistente"].patch(path, json={"sale_price": "3.00", "sale_price_currency": "VES"})
        self.assertEqual(proposed.status_code, 200, proposed.json)
        proposal = proposed.json["pending_change"]
        self.assertEqual(proposal["changes"], {"sale_price": "3.00", "sale_price_currency": "VES"})
        self.assertEqual(proposal["previous_values"], {"sale_price": "3.00", "sale_price_currency": "USD"})
        self.assertEqual(self.clients["admin"].get(path).json["sale_price_currency"], "USD")
        approved = self.clients["admin"].post(path + "/approve", json={"proposal_id": proposal["id"]})
        self.assertEqual(approved.status_code, 200, approved.json)
        changed = self.clients["asistente"].patch(path, json={"description": "Solo descripción"})
        self.assertEqual(changed.status_code, 200, changed.json)
        self.assertEqual(changed.json["pending_change"]["changes"], {"description": "Solo descripción"})
        done = self.clients["admin"].post(path + "/approve", json={"proposal_id": changed.json["pending_change"]["id"]})
        self.assertEqual((done.json["sale_price_currency"], done.json["purchase_price_currency"]), ("VES", "USD"))
        self.assertEqual((done.json["sale_price"], done.json["purchase_price"]), (3, 2))
        self.assertEqual(StockMovement.query.count(), 0)
        self.assertEqual(self.stock(), 10)

    def test_incomplete_pairs_and_concurrent_currency_changes_are_rejected(self):
        path = f"/api/products/{self.product.id}"
        for body in ({"sale_price": "4"}, {"sale_price_currency": "VES"},
                     {"purchase_price": "4", "purchase_price_currency": None}):
            self.assertEqual(self.clients["asistente"].patch(path, json=body).status_code, 400)
        proposed = self.clients["asistente"].patch(path, json={"purchase_price": "2.00", "purchase_price_currency": "VES"})
        self.assertEqual(proposed.status_code, 200, proposed.json)
        self.product.purchase_price_currency = "VES"
        db.session.commit()
        result = self.clients["admin"].post(path + "/approve", json={"proposal_id": proposed.json["pending_change"]["id"]})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(ProductChange.query.one().status, "pending")

    def test_supplier_currency_is_independent_and_conditions_preserve_it(self):
        path = f"/api/products/{self.product.id}/suppliers"
        for currency in (None, "usd", "EUR", "VES "):
            result = self.clients["asistente"].post(path, json={"supplier_id": self.supplier.id,
                "purchase_price": "7.50", "purchase_price_currency": currency})
            self.assertEqual(result.status_code, 400)
        created = self.clients["asistente"].post(path, json={"supplier_id": self.supplier.id,
            "purchase_price": "7.50", "purchase_price_currency": "VES"})
        self.assertEqual(created.status_code, 201, created.json)
        updated = self.clients["asistente"].patch(path + f"/{created.json['id']}", json={"lead_time_days": 4})
        self.assertEqual(updated.status_code, 200, updated.json)
        self.assertEqual((updated.json["purchase_price"], updated.json["purchase_price_currency"]), ("7.50", "VES"))
        self.assertEqual(self.product.purchase_price_currency, "USD")
        self.assertEqual(self.clients["asistente"].patch(path + f"/{created.json['id']}",
            json={"purchase_price": "1.00"}).status_code, 400)
        self.assertEqual(ProductSupplier.query.count(), 1)

    def test_totals_shares_filters_and_exports_separate_currencies(self):
        second = Product(code="SECOND", name="VES", category_id=self.product.category_id, current_stock=4,
            minimum_stock=0, purchase_price=5, purchase_price_currency="VES", sale_price=3, sale_price_currency="VES")
        unknown = Product(code="UNKNOWN", name="Sin moneda", category_id=self.product.category_id, current_stock=2,
            minimum_stock=0, purchase_price=100, sale_price=100)
        db.session.add_all([second, unknown])
        db.session.commit()
        result = self.clients["admin"].get("/api/inventory/summary-dashboard").json["kpis"]["inventory_value"]
        self.assertIsNone(result["value"])
        self.assertEqual(result["totals_by_currency"], [{"currency": "USD", "amount": "20.00"}, {"currency": "VES", "amount": "20.00"}])
        self.assertEqual((result["unknown_currency_products"], result["unknown_currency_units"]), (1, 2))
        category = self.clients["admin"].get(f"/api/categories/{self.product.category_id}").json
        self.assertEqual([item["share"] for item in category["totals_by_currency"]], [100, 100])
        self.assertFalse(category["value_rank_available"])
        self.assertEqual(self.clients["admin"].get("/api/products?price_min=1").status_code, 400)
        selected = self.clients["admin"].get("/api/products?price_min=1&sale_price_currency=VES")
        self.assertEqual([row["id"] for row in selected.json["items"]], [second.id])
        html = self.clients["admin"].get("/inventory/summary").text
        self.assertIn("$ 20.00", html)
        self.assertIn("Bs. 20.00", html)
        self.assertNotIn("$ 240.00", html)
        exported = self.clients["admin"].post("/api/products/export", json={"format": "csv"})
        rows = list(csv.DictReader(StringIO(exported.data.decode("utf-8-sig"))))
        self.assertEqual({row["Moneda de venta"] for row in rows}, {"USD", "VES", "Moneda no registrada"})

    def test_cost_currency_follows_cost_permissions_in_reads_proposals_and_exports(self):
        path = f"/api/products/{self.product.id}"
        self.clients["asistente"].patch(path, json={"purchase_price": "2.50", "purchase_price_currency": "VES"})
        for role in ("inventario", "vendedor"):
            read = self.clients[role].get(path)
            if read.status_code == 200:
                self.assertNotIn("purchase_price", read.json)
                self.assertNotIn("purchase_price_currency", read.json)
                for proposal in (read.json.get("pending_change"), read.json.get("last_change")):
                    if proposal:
                        self.assertNotIn("purchase_price_currency", proposal["changes"])
                        self.assertNotIn("purchase_price_currency", proposal["previous_values"])
            else:
                self.assertEqual(read.status_code, 403)
            exported = self.clients[role].post("/api/products/export", json={"format": "csv"})
            if exported.status_code == 200:
                self.assertNotIn("Moneda de compra", exported.data.decode("utf-8-sig"))
            else:
                self.assertEqual(exported.status_code, 403)

    def test_forms_have_independent_unselected_currency_controls_and_symbols(self):
        html = self.clients["asistente"].get("/products").text
        for control in ("np-price-currency", "np-cost-currency", "pl-field-price-currency", "pl-field-cost-currency"):
            self.assertIn('id="' + control + '"', html)
        self.assertIn('<option value="" selected disabled>Selecciona una moneda</option>', html)
        self.assertEqual(format_amount("1.50", "USD"), "$ 1.50")
        self.assertEqual(format_amount("1.50", "VES"), "Bs. 1.50")

    def test_delivery_guard_rejects_ves_unknown_and_mixed_before_stock_changes(self):
        original = self.stock()
        for currency in ("VES", None):
            self.product.sale_price_currency = currency
            db.session.commit()
            response = self.clients["vendedor"].post("/api/delivery-notes", json={"customer_name": "Cliente sintético",
                "items": [{"product_id": self.product.id, "quantity": 1}]})
            self.assertEqual(response.status_code, 400, response.json)
            self.assertEqual(self.stock(), original)
            self.assertEqual(StockMovement.query.count(), 0)


class PortableMigration(unittest.TestCase):
    def test_sqlite_preserves_original_columns_and_has_exact_nullable_checks_without_defaults(self):
        engine = create_engine("sqlite:///:memory:")
        metadata = MetaData()
        products = Table("products", metadata, Column("id", Integer, primary_key=True),
                         Column("sale_price", Numeric(12, 2)), Column("purchase_price", Numeric(12, 2)))
        links = Table("product_suppliers", metadata, Column("id", Integer, primary_key=True), Column("purchase_price", Numeric(12, 2)))
        metadata.create_all(engine)
        path = Path(__file__).resolve().parents[1] / "migrations/versions/0007_catalog_currency.py"
        spec = importlib.util.spec_from_file_location("catalog_currency_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with engine.begin() as connection:
            connection.execute(products.insert(), {"id": 1, "sale_price": "3.50", "purchase_price": "2.25"})
            connection.execute(links.insert(), {"id": 1, "purchase_price": "1.25"})
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            self.assertEqual(tuple(map(str, connection.execute(text("SELECT sale_price,purchase_price FROM products")).one())), ("3.5", "2.25"))
            for table, fields in migration.FIELDS.items():
                columns = {item["name"]: item for item in inspect(connection).get_columns(table)}
                for field in fields:
                    self.assertTrue(columns[field]["nullable"])
                    self.assertIsNone(columns[field]["default"])
                    for currency in ("USD", "VES", None):
                        connection.execute(text("UPDATE " + table + " SET " + field + "=:currency"), {"currency": currency})
                    for currency in ("usd", "EUR", "VES ", " USD", ""):
                        with self.assertRaises(IntegrityError):
                            connection.execute(text("UPDATE " + table + " SET " + field + "=:currency"), {"currency": currency})
        engine.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)
