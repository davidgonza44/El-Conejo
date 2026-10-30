r"""Isolated service integration: disposable SQLite, no dotenv, MySQL or factory.

Run: .\.venv\Scripts\python.exe -B scripts/test_seed_tesis.py
SQLite cannot prove MySQL locking, DDL or driver behavior; those remain unexecuted.
"""
from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
from datetime import timedelta
import os
from pathlib import Path
import sys
import tempfile
import unicodedata
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from sqlalchemy import BigInteger, event, select, update
from sqlalchemy.ext.compiler import compiles

import seed_tesis as seed
import seed_tesis_safety as safety
from seed_tesis_data import END, START, catalog, commercial_plan, plan_summary, stamp


@compiles(BigInteger, "sqlite")
def sqlite_bigint(_type, _compiler, **_kwargs):
    return "INTEGER"


class SeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guards = ExitStack()
        cls.addClassCleanup(cls.guards.close)
        cls.guards.enter_context(patch("pymysql.connect", side_effect=AssertionError("MySQL forbidden in isolated suite")))
        cls.guards.enter_context(patch("dotenv.load_dotenv", side_effect=AssertionError("dotenv forbidden")))
        cls.domain = safety.load_domain()
        folder = cls.guards.enter_context(tempfile.TemporaryDirectory(prefix="conejo-tesis-tests-"))
        cls.app = Flask("seed_isolated_tests")
        cls.app.config.update(TESTING=True, SECRET_KEY="isolated-only", SQLALCHEMY_DATABASE_URI="sqlite:///" + str(Path(folder) / "isolated.sqlite"),
                              SQLALCHEMY_TRACK_MODIFICATIONS=False)
        cls.domain.db.init_app(cls.app)
        cls.domain.login_manager.init_app(cls.app)
        cls.app_context = cls.app.app_context()
        cls.app_context.push()
        engine = cls.domain.db.engine
        cls.guards.callback(engine.dispose)
        cls.guards.callback(cls.app_context.pop)
        cls.guards.callback(cls.domain.db.session.remove)

        @event.listens_for(engine, "connect")
        def sqlite_connect(dbapi, _record):
            dbapi.isolation_level = None
            dbapi.execute("PRAGMA foreign_keys=ON")

        @event.listens_for(engine, "begin")
        def sqlite_begin(connection):
            connection.exec_driver_sql("BEGIN")

        cls.domain.db.create_all()
        safety.manifest_table().create(engine)
        with cls.app.test_request_context(), engine.connect() as connection:
            created = {name: set() for name in safety.TABLES}
            with connection.begin(), safety.domain_session(cls.domain, connection, created):
                safety.validate_domain_schema(connection, cls.domain)
                safety.validate_registry_schema(connection)
                cls.manifest, changed = seed.seed_transaction(connection, cls.domain, created)
                assert changed

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(self.app.test_request_context())
        self.connection = self.stack.enter_context(self.domain.db.engine.connect())
        transaction = self.connection.begin()
        self.stack.callback(transaction.rollback)
        self.created = {name: set() for name in safety.TABLES}
        self.stack.enter_context(safety.domain_session(self.domain, self.connection, self.created))

    def rows(self, name):
        return safety.select_rows(self.domain, name, self.manifest["payload"]["rows"][name])

    def assert_reset_rejected(self):
        before = {name: [dict(r) for r in self.rows(name)] for name in safety.TABLES}
        with self.assertRaises(safety.SeedError):
            seed.seed_transaction(self.connection, self.domain, self.created, reset=True)
        after = {name: [dict(r) for r in self.rows(name)] for name in safety.TABLES}
        self.assertEqual(before, after)

    def test_plan_and_balances(self):
        plan = commercial_plan()
        self.assertEqual(plan, commercial_plan())
        seed.validate_generated(self.domain, self.manifest["payload"]["rows"], plan)
        self.assertGreater(plan_summary(plan)["exhausted"], 0)
        self.assertGreater(plan_summary(plan)["low_stock"], 0)
        self.assertEqual(len({p["name"] for p in catalog()}), 120)
        self.assertEqual(len({p["code"] for p in catalog()}), 120)

    def test_visible_fields_and_inactive_actor(self):
        forbidden = ("demo", "sintetic", "synthetic", "seed", "manifest", "hash", "20260921")
        for table in safety.TABLES:
            for row in self.rows(table):
                for field, raw in row.items():
                    value = ''.join(c for c in unicodedata.normalize('NFKD', str(raw or '').casefold())
                                    if not unicodedata.combining(c))
                    self.assertFalse(any(term in value for term in forbidden), (table, field))
        actors = self.rows("users")
        self.assertEqual(len(actors), 3)
        self.assertEqual(sum(bool(a["is_active"]) for a in actors), 2)
        self.assertTrue(all(a["password_hash"] is None and not a["email_verified"] for a in actors))

    def test_states_and_payments(self):
        for table, states in (("sales", {"completed"}), ("orders", {"pending", "processing", "completed", "cancelled"}),
            ("purchase_orders", {"pending_approval", "partial", "completed", "cancelled"}),
            ("quotes", {"pending", "sent", "accepted", "rejected"})):
            self.assertEqual({r["status"] for r in self.rows(table)}, states)
        self.assertEqual(Counter(r["status"] for r in self.rows("orders")),
                         dict(pending=10, processing=10, completed=10, cancelled=10))
        self.assertEqual({r["payment_status"] for r in self.rows("orders")}, {"pending", "paid"})
        self.assertEqual({r["payment_method"] for r in self.rows("sales")}, {"cash", "transfer", "debit_card", "credit_card"})
        quotes = self.domain.models.Quote.query.all()
        self.assertEqual(Counter(q.effective_status(END + timedelta(days=1)) for q in quotes),
                         dict(pending=8, sent=8, accepted=8, rejected=8, expired=8))

    def test_document_links_and_totals(self):
        for sale in self.domain.models.Sale.query.all():
            self.assertEqual(sale.total, sum(line.line_total for line in sale.lines))
            for line in sale.lines:
                self.assertEqual(line.stock_movement.quantity, line.quantity)
                self.assertEqual(line.stock_movement.reason, f"Venta {sale.sale_number}")
                self.assertEqual(line.stock_movement.product_id, line.product_id)
        for order in self.domain.models.PurchaseOrder.query.all():
            self.assertEqual(order.total, sum(line.line_total for line in order.lines))
            for reception in order.receptions:
                self.assertGreaterEqual(reception.received_at, order.commercial_order_date)
                for line in reception.lines:
                    movement = self.domain.db.session.get(self.domain.models.StockMovement, line.stock_movement_id)
                    self.assertEqual(movement.quantity, line.quantity)
                    self.assertEqual(movement.reason, f"Recepción {reception.id} · {order.order_number}")
        adjustments = [r for r in self.rows("stock_movements") if r["movement_type"] == "ajuste"]
        self.assertEqual(len(adjustments), 8)
        self.assertTrue(all(r["quantity"] == 1 for r in adjustments))

    def test_history_and_no_scientific_data(self):
        sales = self.rows("sales")
        self.assertEqual(min(s["sold_at"].date() for s in sales), START)
        self.assertEqual(max(s["sold_at"].date() for s in sales), END)
        self.assertEqual(len({s["sold_at"].date() for s in sales}), 263)
        self.assertEqual(self.domain.models.HistoricalImport.query.count(), 0)
        self.assertEqual(self.domain.models.HistoricalDemandRecord.query.count(), 0)
        self.assertEqual(self.domain.models.Notification.query.count(), 0)

    def test_predictive_source_and_eligibility(self):
        from app.services import predictive_data_service as predictive, demand_data_service as demand

        events = demand.load_demand_events()
        issued_notes = self.domain.models.DeliveryNoteItem.query.join(self.domain.models.DeliveryNote).filter(
            self.domain.models.DeliveryNote.status == 'issued').all()
        self.assertEqual(sum(e.quantity for e in events), sum(r["quantity"] for r in self.rows("sale_lines"))
                         + sum(item.quantity for item in issued_notes))
        series = predictive.load_series(today=END + timedelta(days=1))
        product_id = next(p['id'] for p in self.rows("products") if p['code'] == 'CEM-003')
        self.assertEqual(predictive.classify_eligibility(series[product_id]).status, predictive.ELIGIBLE)
        self.assertNotIn(self.rows("products")[-1]["id"], series)

    def test_predictive_variety_across_categories(self):
        from app.services import predictive_data_service as predictive

        series = predictive.load_series(today=END + timedelta(days=2))
        classifications, categories, suffixes = Counter(), set(), set()
        for product in self.domain.models.Product.query.all():
            history = series.get(product.id, predictive.TimeSeries(product.id, 'daily'))
            status = predictive.classify_eligibility(history).status
            classifications[status] += 1
            if status == predictive.ELIGIBLE:
                categories.add(product.category_id)
                suffixes.add(product.code.rsplit('-', 1)[1])
        self.assertEqual(classifications[predictive.ELIGIBLE], 18)
        expected = {c.id for c in self.domain.models.Category.query.all() if c.name != 'Cerraduras'}
        self.assertEqual(categories, expected)
        self.assertGreaterEqual(len(suffixes), 6)
        for status in (predictive.INTERMITTENT, predictive.INSUFFICIENT_HISTORY, predictive.INACTIVE, predictive.NO_DEMAND):
            self.assertGreater(classifications[status], 0, status)

    def test_independent_sales_payments_customers_and_responsibles(self):
        independent, payments = Counter(), Counter()
        for sale in self.domain.models.Sale.query.all():
            categories = {line.product.category_id for line in sale.lines}
            if len(categories) == 1:
                independent.update(categories)
            payments[sale.payment_method] += 1
        self.assertEqual(len(independent), 10)
        self.assertGreaterEqual(min(independent.values()), 15)
        self.assertGreater(max(payments.values()), 3 * min(payments.values()))
        for model in (self.domain.models.Quote, self.domain.models.Order):
            names = [row.customer.name.rsplit(' ', 1)[-1] for row in model.query.order_by(model.id).limit(10)]
            self.assertGreaterEqual(len(set(names)), 5)
        active_ids = {a['id'] for a in self.rows('users') if a['is_active']}
        recent = [m for m in self.rows('stock_movements') if m['created_at'].date() >= END - timedelta(days=30)]
        self.assertTrue(all(m['user_id'] in active_ids for m in recent))
        self.assertEqual(len({m['user_id'] for m in recent}), 2)

    def test_idempotent(self):
        record, changed = seed.seed_transaction(self.connection, self.domain, self.created)
        self.assertFalse(changed)
        self.assertEqual(record, self.manifest)
        self.assertTrue(all(not ids for ids in self.created.values()))

    def test_modified_record_blocks_reset(self):
        product = self.domain.models.Product.query.first()
        product.name = "Artículo corregido por usuario"
        self.domain.db.session.commit()
        self.assert_reset_rejected()

    def test_missing_record_blocks_reset(self):
        from sqlalchemy import delete

        target = self.domain.db.metadata.tables["quote_lines"]
        self.domain.db.session.execute(delete(target).where(target.c.id == self.rows("quote_lines")[0]["id"]))
        self.domain.db.session.commit()
        self.assert_reset_rejected()

    def test_external_foreign_key_blocks_reset(self):
        product = self.domain.models.Product.query.first()
        self.domain.db.session.add(self.domain.models.Product(name="Artículo ajeno", code="EXT-001", category_id=product.category_id))
        self.domain.db.session.commit()
        self.created = {name: set() for name in safety.TABLES}
        self.assert_reset_rejected()

    def test_state_history_baselines(self):
        payload = self.manifest["payload"]
        owned = payload["rows"]["product_state_histories"]
        self.assertEqual(len(owned), 120)
        self.assertEqual(safety.fingerprints(self.domain, payload["rows"])["product_state_histories"],
                         payload["hashes"]["product_state_histories"])
        when = stamp(START)
        users = {row["id"] for row in self.rows("users")}
        products = {row["id"] for row in self.rows("products")}
        histories = self.domain.models.ProductStateHistory.query.all()
        self.assertEqual({row.product_id for row in histories}, products)
        for row in histories:
            self.assertEqual(row.event_type, "baseline")
            self.assertEqual(row.previous_minimum_stock, row.new_minimum_stock)
            self.assertEqual(bool(row.previous_is_active), bool(row.new_is_active))
            self.assertEqual(row.changed_at, when)
            self.assertIn(row.changed_by_user_id, users)

    def test_external_state_history_blocks_reset(self):
        from sqlalchemy import insert

        product_id = self.rows("products")[0]["id"]
        table = self.domain.models.ProductStateHistory.__table__
        self.domain.db.session.execute(insert(table).values(
            product_id=product_id, previous_minimum_stock=1, new_minimum_stock=2,
            previous_is_active=True, new_is_active=False, event_type="change",
            changed_at=stamp(START), changed_by_user_id=self.rows("users")[0]["id"]))
        self.domain.db.session.commit()
        self.assertEqual(self.domain.models.ProductStateHistory.query.count(), 121)
        self.assert_reset_rejected()
        self.assertEqual(self.domain.models.ProductStateHistory.query.count(), 121)

    def test_v2_manifest_is_rejected_without_deletion(self):
        target = safety.manifest_table()
        self.domain.db.session.execute(update(target).values(version=2))
        self.domain.db.session.commit()
        preserved = dict(self.domain.db.session.execute(select(target)).mappings().one())
        self.assertEqual(preserved["version"], 2)
        self.assert_reset_rejected()
        self.assertEqual(dict(self.domain.db.session.execute(select(target)).mappings().one()), preserved)

    def test_external_notification_blocks_reset(self):
        from sqlalchemy import insert

        table = self.domain.models.Notification.__table__
        # Core insertion bypasses tracker solely to simulate a later external operation.
        user = self.domain.models.User.__table__
        result = self.domain.db.session.execute(insert(user).values(name="Responsable externo", username="external",
            email="external@example.invalid", role="admin", is_active=False))
        self.domain.db.session.execute(insert(table).values(user_id=result.inserted_primary_key[0],
            event="low_stock", title="Revisión", message="Revisar existencia", entity_type="product",
            entity_id=self.rows("products")[0]["id"]))
        self.domain.db.session.commit()
        self.assert_reset_rejected()

    def test_manifest_corruption_blocks_reset(self):
        target = safety.manifest_table()
        self.domain.db.session.execute(update(target).values(payload_sha256="0" * 64))
        self.domain.db.session.commit()
        self.assert_reset_rejected()

    def test_unknown_version_and_ids(self):
        record = deepcopy(self.manifest)
        record["version"] = 999
        with self.assertRaises(safety.SeedError):
            safety.validate_manifest(record)
        record = deepcopy(self.manifest)
        record["payload"]["rows"]["products"] = [True]
        record["payload_sha256"] = safety.digest(record["payload"])
        with self.assertRaises(safety.SeedError):
            safety.validate_manifest(record)

    def test_missing_manifest_does_not_adopt(self):
        from sqlalchemy import delete

        self.domain.db.session.execute(delete(safety.manifest_table()))
        self.domain.db.session.commit()
        self.assert_reset_rejected()

    def test_reset_intact(self):
        old = self.manifest
        new, changed = seed.seed_transaction(self.connection, self.domain, self.created, reset=True)
        self.assertTrue(changed)
        self.assertNotEqual(new["run_uuid"], old["run_uuid"])
        self.assertEqual(new["environment_uuid"], old["environment_uuid"])
        self.assertEqual({k: len(v) for k, v in new["payload"]["rows"].items()},
                         {k: len(v) for k, v in old["payload"]["rows"].items()})

    def test_failure_after_reset_rolls_back_everything(self):
        # Outer SAVEPOINT simulates the production transaction; all service commits remain nested.
        before = safety.fingerprints(self.domain, self.manifest["payload"]["rows"])
        self.domain.db.session.remove()
        savepoint = self.connection.begin_nested()
        with patch.object(seed, "generate", side_effect=RuntimeError("injected failure")):
            with self.assertRaises(RuntimeError):
                seed.seed_transaction(self.connection, self.domain, self.created, reset=True)
        self.domain.db.session.remove()
        savepoint.rollback()
        self.assertEqual(safety.fingerprints(self.domain, self.manifest["payload"]["rows"]), before)
        self.assertEqual(safety.existing_manifest(self.domain), self.manifest)


class ConnectionGuards(unittest.TestCase):
    def test_mariadb_json_requires_constraint(self):
        from unittest.mock import Mock
        from sqlalchemy.dialects.mysql import LONGTEXT, VARCHAR

        connection = Mock()
        for clauses, expected in ((['json_valid(`payload`)'], True), ([], False), (['1 = 1'], False)):
            connection.execute.return_value.scalars.return_value = clauses
            self.assertEqual(safety.mariadb_json_payload(connection, LONGTEXT()), expected)
        self.assertFalse(safety.mariadb_json_payload(connection, VARCHAR()))

    def test_standalone_registry_preparation(self):
        from sqlalchemy import create_engine, inspect
        from prepare_seed_tesis import prepare_registry

        engine = create_engine("sqlite://")
        try:
            with engine.connect() as connection:
                self.assertTrue(prepare_registry(connection))
                self.assertFalse(prepare_registry(connection))
                self.assertEqual(inspect(connection).get_table_names(), ["seed_manifests"])
                self.assertEqual(connection.execute(select(safety.manifest_table())).all(), [])
                connection.exec_driver_sql("ALTER TABLE seed_manifests ADD COLUMN unexpected INTEGER")
                with self.assertRaises(safety.SeedError):
                    prepare_registry(connection)
        finally:
            engine.dispose()

    def test_effective_database_guard(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        connection = Mock(dialect=SimpleNamespace(name="mysql"))
        connection.execute.return_value.scalar.return_value = "ferreteria_conejo"
        with self.assertRaises(safety.SeedError):
            safety.mysql_preflight(connection)
        self.assertEqual(connection.execute.call_count, 1)
        self.assertEqual(str(connection.execute.call_args.args[0]), "SELECT DATABASE()")

    def test_wrong_target_before_network(self):
        for host, name in (("localhost", "ferreteria_conejo"), ("127.0.0.1", safety.DATABASE),
                           ("remote", safety.DATABASE), ("localhost", "other"), ("", "")):
            with patch.dict(os.environ, {"DB_HOST": host, "DB_NAME": name}, clear=True), \
                 patch.object(safety.socket, "getaddrinfo", side_effect=AssertionError("network forbidden")):
                with self.assertRaises(safety.SeedError):
                    safety.configured_url()

    def test_conflicting_url_and_remote_resolution(self):
        env = dict(DB_HOST="localhost", DB_NAME=safety.DATABASE, DB_USER="root", DB_PORT="3306", DB_ALLOW_EMPTY_PASSWORD="1")
        with patch.dict(os.environ, dict(env, DATABASE_URL="not-allowed"), clear=True):
            with self.assertRaises(safety.SeedError):
                safety.configured_url()

    def test_password_modes(self):
        env = dict(DB_HOST="localhost", DB_NAME=safety.DATABASE, DB_USER="root", DB_PORT="3306")
        addresses = [(2, 1, 6, "", ("127.0.0.1", 3306))]
        for extra, expected in ((dict(DB_PASSWORD="isolated-test-only"), "isolated-test-only"),
                                (dict(DB_ALLOW_EMPTY_PASSWORD="1"), ""),
                                (dict(DB_ALLOW_EMPTY_PASSWORD="1", DB_PASSWORD=""), "")):
            with patch.dict(os.environ, dict(env, **extra), clear=True), \
                 patch.object(safety.socket, "getaddrinfo", return_value=addresses):
                self.assertEqual(safety.configured_url().password, expected)
        for extra in ({}, dict(DB_PASSWORD=""), dict(DB_ALLOW_EMPTY_PASSWORD="0"),
                      dict(DB_PASSWORD="", DB_ALLOW_EMPTY_PASSWORD="true")):
            with patch.dict(os.environ, dict(env, **extra), clear=True), self.assertRaises(safety.SeedError):
                safety.configured_url()
        for extra in (dict(DB_USER=""), dict(DB_USER="other"), dict(DB_PORT="3307")):
            with patch.dict(os.environ, dict(env, DB_ALLOW_EMPTY_PASSWORD="1") | extra, clear=True), \
                 self.assertRaises(safety.SeedError):
                safety.configured_url()
        with patch.dict(os.environ, dict(env, DB_ALLOW_EMPTY_PASSWORD="1"), clear=True), patch.object(safety.socket, "getaddrinfo",
            return_value=[(2, 1, 6, "", ("203.0.113.1", 3306))]):
            with self.assertRaises(safety.SeedError):
                safety.configured_url()


if __name__ == "__main__":
    unittest.main(verbosity=2)
