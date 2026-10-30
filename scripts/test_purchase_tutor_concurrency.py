"""Concurrencia de las correcciones del tutor en MariaDB/MySQL desechable (REPEATABLE READ por defecto).

Requiere una base conejo_tutor_test_<12 hex> en 127.0.0.1 y DB_PORT, DB_PASSWORD (DB_USER opcional).
La suite BORRA y recrea esa base: nunca apunte a DEMO 5001, a la base de tesis ni a datos reales.
SQLite no acredita estos resultados.

Retenciones deterministas: el primer hilo se detiene dentro de su transacción, con el bloqueo
tomado, y avisa; solo entonces arranca el segundo, que debe esperar ese bloqueo.
"""
import os
import re
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE = os.environ.get("TUTOR_TEST_DATABASE", "")
if not re.fullmatch(r"conejo_tutor_test_[a-f0-9]{12}", DATABASE) or os.environ.get("DB_HOST", "127.0.0.1") != "127.0.0.1":
    raise SystemExit("Solo una base desechable conejo_tutor_test_<hex> en 127.0.0.1.")

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL

from app import create_app
from app.extensions import db
from app.models import Category, Product, StockMovement, User
from app.models.purchase import ProductSupplier, PurchaseOrder, PurchaseReception, Supplier
from app.models.replenishment_request import ReplenishmentRequest
from app.models.supplier_quote import SupplierQuote
from app.services import (purchase_service, rbac_service, replenishment_request_service, session_service,
                          supplier_quote_service)
from app.utils.schema import reset_cache
import migrate_replenishment_source_order as migration
from migrate_purchase_orders import engine_for

SERVER = URL.create("mysql+pymysql", username=os.environ.get("DB_USER", "root"), password=os.environ["DB_PASSWORD"],
                    host="127.0.0.1", port=int(os.environ["DB_PORT"]), query={"charset": "utf8mb4"})
HOLD_SECONDS = 2.0


class MariaConfig:
    TESTING = True
    SECRET_KEY = "disposable-tutor-concurrency"
    APP_ENV = "testing"
    SQLALCHEMY_DATABASE_URI = SERVER.set(database=DATABASE).render_as_string(hide_password=False)
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    MAIL_ENABLED = False
    NOTIFICATIONS_DRY_RUN = True


def caracas_today():
    return datetime.now(ZoneInfo("America/Caracas")).date().isoformat()


@contextmanager
def hold(owner, name, thread=None):
    """The first matching call signals, then sleeps inside the caller's transaction."""
    real = getattr(owner, name)
    entered, guard, state = threading.Event(), threading.Lock(), {"used": False}

    def wrapper(*args, **kwargs):
        if thread is None or threading.current_thread().name == thread:
            with guard:
                first, state["used"] = not state["used"], True
            if first:
                entered.set()
                time.sleep(HOLD_SECONDS)
        return real(*args, **kwargs)

    with patch.object(owner, name, wrapper):
        yield entered


def run_threads(*calls):
    """calls: (name, fn). Returns results by name; fn returns (status, json)."""
    results, threads = {}, []
    for name, fn in calls:
        def target(fn=fn, name=name):
            results[name] = fn()
        threads.append(threading.Thread(target=target, name=name))
    return results, threads


class ConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server = create_engine(SERVER)
        with server.begin() as connection:
            # MySQL 8 types CHECK literals with utf8mb4_0900_ai_ci, so the HEX(...) IN (...) constraints
            # only compile in a database with that collation. MariaDB keeps utf8mb4_unicode_ci.
            mysql8 = "mariadb" not in connection.execute(text("SELECT VERSION()")).scalar().lower()
            collation = "utf8mb4_0900_ai_ci" if mysql8 else "utf8mb4_unicode_ci"
            connection.execute(text(f"DROP DATABASE IF EXISTS {DATABASE}"))
            connection.execute(text(f"CREATE DATABASE {DATABASE} CHARACTER SET utf8mb4 COLLATE {collation}"))
        server.dispose()
        cls.guards = [patch("app.services.notification_providers.send", side_effect=AssertionError("External delivery forbidden"))]
        for guard in cls.guards:
            guard.start()
        cls.app = create_app(MariaConfig)
        cls.app.logger.disabled = True
        from flask import g

        def fresh_request_cache():
            g.pop("_login_user", None)
            rbac_service.invalidate_cache()

        cls.app.before_request_funcs[None].insert(0, fresh_request_cache)
        cls.ctx = cls.app.app_context()
        cls.ctx.push()
        reset_cache()
        db.create_all()
        cls.migration_log = cls.exercise_migration()
        rbac_service.ensure_seeded()
        users = [User(name=name, username=name, email=name + "@example.invalid", role=role)
                 for name, role in (("admin", "admin"), ("inventario", "inventario"), ("inventario2", "inventario"),
                                    ("asistente", "asistente"))]
        cls.category = Category(name="Concurrencia desechable")
        cls.supplier = Supplier(name="Proveedor concurrente", tax_id="TEST-CONC")
        db.session.add_all([*users, cls.category, cls.supplier])
        db.session.commit()
        cls.users = {user.username: user.id for user in users}

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.engine.dispose()
        cls.ctx.pop()
        for guard in cls.guards:
            guard.stop()

    @classmethod
    def exercise_migration(cls):
        """create_all already has the column: roll back, plan, apply twice, and keep the migrated schema."""
        engine = engine_for(DATABASE)
        try:
            removed = migration.rollback(engine, DATABASE)
            with engine.connect() as connection:
                pending = migration.plan(connection)
            applied, linked = migration.migrate(engine, DATABASE)
            again, _ = migration.migrate(engine, DATABASE)
            columns = {column["name"] for column in inspect(engine).get_columns("replenishment_requests")}
        finally:
            engine.dispose()
        assert len(removed) == 3 and len(pending) == 3 and len(applied) == 3, (removed, pending, applied)
        assert again == [] and linked == 0 and "source_purchase_order_id" in columns
        db.engine.dispose()
        return {"rollback": removed, "plan": pending, "apply": applied, "second_apply": again}

    # ---------- fixtures through the real flow ----------
    def client(self, username):
        from flask import g, session
        from flask_wtf.csrf import generate_csrf
        user = db.session.get(User, self.users[username])
        client = self.app.test_client()
        with self.app.test_request_context():
            session_service.open_session(user)
            g.pop("csrf_token", None)
            csrf = generate_csrf()
            cookies = dict(session)
        with client.session_transaction() as stored:
            stored.update(cookies)
        client.environ_base["HTTP_X_CSRFTOKEN"] = csrf
        db.session.commit()
        return client

    def call(self, username, method, path, body=None):
        return self.later(username, method, path, body)()

    def later(self, username, method, path, body=None):
        """Build the client in this thread; the returned call only performs the HTTP request."""
        client = self.client(username)

        def send():
            response = getattr(client, method)(path, json=body if body is not None else {})
            return response.status_code, response.get_json()
        return send

    def ok(self, expected, result):
        self.assertEqual(result[0], expected, result[1])
        return result[1]

    def product(self, code):
        product = Product(code=code, name="Producto " + code, category_id=self.category.id, current_stock=10,
                          minimum_stock=0, purchase_price=2, sale_price=3, purchase_price_currency="USD",
                          sale_price_currency="USD", is_active=True)
        db.session.add(product)
        db.session.flush()
        db.session.add(ProductSupplier(product_id=product.id, supplier_id=self.supplier.id, purchase_price=2,
                                       purchase_price_currency="USD", is_active=True, is_preferred=False))
        db.session.commit()
        return product.id

    def draft(self, product_id, quantity=10):
        row = self.ok(201, self.call("inventario", "post", "/api/replenishment-requests",
                                     {"items": [{"product_id": product_id, "quantity": quantity}]}))
        self.ok(200, self.call("asistente", "post", f"/api/replenishment-requests/{row['id']}/start"))
        quote = self.ok(201, self.call("asistente", "post", f"/api/replenishment-requests/{row['id']}/quotes", {
            "supplier_id": self.supplier.id, "currency": "VES", "issued_on": caracas_today(), "valid_until": "2099-01-01",
            "items": [{"product_id": product_id, "unit_price": "10.00"}]}))
        prepared = self.ok(200, self.call("asistente", "post", f"/api/replenishment-requests/{row['id']}/prepare",
                                          {"supplier_quote_id": quote["id"]}))
        return self.ok(200, self.call("asistente", "get", f"/api/purchases/{prepared['purchase_order']['id']}"))

    def approved(self, product_id, quantity=10):
        order = self.draft(product_id, quantity)
        return self.ok(200, self.call("admin", "post", f"/api/purchases/{order['id']}/approve"))

    def closed_partial(self, product_id):
        order = self.approved(product_id)
        return self.ok(200, self.call("inventario", "post", f"/api/purchases/{order['id']}/receptions", {
            "received_at": caracas_today(), "items": [{"line_id": order["items"][0]["id"], "quantity": 6}],
            "confirm_partial_closure": True, "closure_reason": "El proveedor solo despachó 6 unidades."}))

    def fresh(self):
        db.session.rollback()
        db.session.expire_all()

    def shortage_count(self, order_id):
        self.fresh()
        return ReplenishmentRequest.query.filter_by(source_purchase_order_id=order_id).count()

    def sequenced(self, first, second, entered):
        """Start first; once it holds its lock, start second."""
        results, threads = run_threads(first, second)
        threads[0].start()
        self.assertTrue(entered.wait(60), "first thread never reached its hold")
        threads[1].start()
        for thread in threads:
            thread.join(120)
            self.assertFalse(thread.is_alive(), "thread did not finish: possible deadlock")
        return results

    # ---------- shortage requests ----------
    def test_same_user_retries_while_the_first_holds_the_lock(self):
        order = self.closed_partial(self.product("SR-SAME"))
        path = f"/api/replenishment-requests/from-order/{order['id']}"
        with hold(replenishment_request_service, "_request_lines") as entered:
            results = self.sequenced(("first", self.later("inventario", "post", path)),
                                     ("retry", self.later("inventario", "post", path)), entered)
        self.assertEqual([results[name][0] for name in ("first", "retry")], [201, 201], results)
        self.assertEqual(results["first"][1]["id"], results["retry"][1]["id"])
        self.assertEqual(self.shortage_count(order["id"]), 1)

    def test_two_users_at_once_create_one_request(self):
        order = self.closed_partial(self.product("SR-TWO"))
        path = f"/api/replenishment-requests/from-order/{order['id']}"
        with hold(replenishment_request_service, "_request_lines") as entered:
            results = self.sequenced(("first", self.later("inventario", "post", path)),
                                     ("other", self.later("inventario2", "post", path)), entered)
        self.assertEqual((results["first"][0], results["other"][0]), (201, 409), results)
        self.assertIn(results["first"][1]["request_number"], results["other"][1]["error"])
        self.assertEqual(self.shortage_count(order["id"]), 1)

    def test_burst_of_simultaneous_attempts_creates_one_request(self):
        order = self.closed_partial(self.product("SR-BURST"))
        path = f"/api/replenishment-requests/from-order/{order['id']}"
        barrier = threading.Barrier(10)

        def attempt(send):
            barrier.wait(30)
            return send()

        results, threads = run_threads(*[
            (f"t{i}", lambda send=self.later("inventario" if i % 2 else "inventario2", "post", path): attempt(send))
            for i in range(10)])
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
            self.assertFalse(thread.is_alive())
        statuses = sorted(status for status, _ in results.values())
        self.assertTrue(set(statuses) <= {201, 409}, results)
        created = {payload["id"] for status, payload in results.values() if status == 201}
        self.assertEqual(len(created), 1, results)
        self.assertEqual(self.shortage_count(order["id"]), 1)

    # ---------- reception ----------
    def test_concurrent_double_reception_records_one(self):
        product_id = self.product("RC-DOUBLE")
        order = self.approved(product_id)
        self.fresh()
        movements = StockMovement.query.filter_by(product_id=product_id).count()
        body = {"received_at": caracas_today(), "items": [{"line_id": order["items"][0]["id"], "quantity": 10}]}
        path = f"/api/purchases/{order['id']}/receptions"
        with hold(purchase_service, "_partial_closure_reason") as entered:
            results = self.sequenced(("first", self.later("inventario", "post", path, body)),
                                     ("second", self.later("inventario", "post", path, body)), entered)
        self.assertEqual((results["first"][0], results["second"][0]), (200, 409), results)
        self.fresh()
        self.assertEqual(db.session.get(Product, product_id).current_stock, 20)
        self.assertEqual(StockMovement.query.filter_by(product_id=product_id).count(), movements + 1)
        self.assertEqual(PurchaseReception.query.filter_by(purchase_order_id=order["id"]).count(), 1)

    # ---------- approval against quote changes ----------
    def approval_vs(self, change_name, change, holder):
        order = self.draft(self.product("AP-" + change_name.upper() + holder.upper()))
        approve = ("approve", self.later("admin", "post", f"/api/purchases/{order['id']}/approve"))
        other = (change_name, change(order))
        if holder == "approve":
            with hold(supplier_quote_service, "order_mismatch", thread="approve") as entered:
                results = self.sequenced(approve, other, entered)
        else:
            owner, name = {"cancel": (SupplierQuote, "effective_status"),
                           "replace": (supplier_quote_service, "_lines")}[change_name]
            with hold(owner, name, thread=change_name) as entered:
                results = self.sequenced(other, approve, entered)
        self.fresh()
        return order, results, db.session.get(PurchaseOrder, order["id"]), db.session.get(SupplierQuote, order["supplier_quote_id"])

    def cancel_quote(self, order):
        return self.later("asistente", "post", f"/api/supplier-quotes/{order['supplier_quote_id']}/cancel")

    def replace_quote(self, order):
        return self.later("asistente", "post", "/api/supplier-quotes", {
            "supplier_id": self.supplier.id, "issued_on": caracas_today(), "currency": "VES", "valid_until": "2099-01-01",
            "replaces_quote_id": order["supplier_quote_id"],
            "items": [{"product_id": order["items"][0]["product_id"], "quantity": 10, "unit_price": "12.00"}]})

    def assert_kept(self, row, order):
        self.assertEqual((row.supplier_quote_id, row.lines[0].unit_cost, row.lines[0].quantity_ordered, row.total),
                         (order["supplier_quote_id"], 10, 10, 100))

    def test_approval_first_then_cancellation_keeps_the_approved_order(self):
        order, results, row, quote = self.approval_vs("cancel", self.cancel_quote, holder="approve")
        self.assertEqual((results["approve"][0], results["cancel"][0]), (200, 200), results)
        self.assertEqual((row.status, quote.status), ("approved", "cancelada"))
        self.assert_kept(row, order)

    def test_cancellation_first_blocks_approval(self):
        order, results, row, quote = self.approval_vs("cancel", self.cancel_quote, holder="cancel")
        self.assertEqual((results["cancel"][0], results["approve"][0]), (200, 409), results)
        self.assertEqual((row.status, row.approved_at, quote.status), ("pending_approval", None, "cancelada"))

    def test_approval_first_then_replacement_keeps_the_approved_order(self):
        order, results, row, quote = self.approval_vs("replace", self.replace_quote, holder="approve")
        self.assertEqual((results["approve"][0], results["replace"][0]), (200, 201), results)
        self.assertEqual((row.status, quote.status), ("approved", "reemplazada"))
        self.assert_kept(row, order)

    def test_replacement_first_blocks_approval(self):
        order, results, row, quote = self.approval_vs("replace", self.replace_quote, holder="replace")
        self.assertEqual((results["replace"][0], results["approve"][0]), (201, 409), results)
        self.assertEqual((row.status, quote.status), ("pending_approval", "reemplazada"))

    # ---------- lock order (seguimiento 17 oct 2026) ----------
    # Each case holds the first transaction right after its first locks and before the next ones.
    # With the old lock order the second transaction took the other resource and InnoDB aborted one
    # with a deadlock (409); with supplier -> quote -> products ascending, the second only waits.
    @staticmethod
    def deadlocks():
        """InnoDB deadlock counter: MySQL 8 exposes it in INNODB_METRICS, MariaDB as a status variable."""
        server = create_engine(SERVER)
        try:
            with server.connect() as connection:
                row = connection.execute(text("SHOW GLOBAL STATUS LIKE 'Innodb_deadlocks'")).first()
                if row is None:
                    row = connection.execute(text(
                        "SELECT NAME, COUNT FROM information_schema.INNODB_METRICS WHERE NAME = 'lock_deadlocks'")).first()
                return int(row[1])
        finally:
            server.dispose()

    def sequenced_without_deadlock(self, first, second, entered):
        """Lock-order cases: the controller retries deadlocks, so only the counter can prove none happened."""
        before = self.deadlocks()
        results = self.sequenced(first, second, entered)
        self.assertEqual(self.deadlocks(), before, "InnoDB detected a deadlock: lock order regressed")
        return results

    def multi_draft(self, product_ids):
        row = self.ok(201, self.call("inventario", "post", "/api/replenishment-requests",
                                     {"items": [{"product_id": key, "quantity": 5} for key in product_ids]}))
        self.ok(200, self.call("asistente", "post", f"/api/replenishment-requests/{row['id']}/start"))
        quote = self.ok(201, self.call("asistente", "post", f"/api/replenishment-requests/{row['id']}/quotes", {
            "supplier_id": self.supplier.id, "currency": "VES", "issued_on": caracas_today(), "valid_until": "2099-01-01",
            "items": [{"product_id": key, "unit_price": "10.00"} for key in product_ids]}))
        prepared = self.ok(200, self.call("asistente", "post", f"/api/replenishment-requests/{row['id']}/prepare",
                                          {"supplier_quote_id": quote["id"]}))
        return self.ok(200, self.call("asistente", "get", f"/api/purchases/{prepared['purchase_order']['id']}"))

    def second_supplier(self, product_ids):
        self.fresh()
        supplier = Supplier.query.filter_by(tax_id="TEST-CONC-2").first()
        if supplier is None:
            supplier = Supplier(name="Segundo proveedor concurrente", tax_id="TEST-CONC-2")
            db.session.add(supplier)
            db.session.flush()
        for key in product_ids:
            db.session.add(ProductSupplier(product_id=key, supplier_id=supplier.id, purchase_price=2,
                                           purchase_price_currency="USD", is_active=True, is_preferred=False))
        db.session.commit()
        return supplier.id

    def test_prepare_with_a_shared_quote_while_approving(self):
        # Approval's locking read of its lines gap-locks the index end where the new lines go,
        # so InnoDB may abort one side; the controller retries it and both complete.
        product_id = self.product("LK-SHARED")
        order = self.draft(product_id)
        row = self.ok(201, self.call("inventario", "post", "/api/replenishment-requests",
                                     {"items": [{"product_id": product_id, "quantity": 10}]}))
        self.ok(200, self.call("asistente", "post", f"/api/replenishment-requests/{row['id']}/start"))
        prepare = ("prepare", self.later("asistente", "post", f"/api/replenishment-requests/{row['id']}/prepare",
                                         {"supplier_quote_id": order["supplier_quote_id"]}))
        approve = ("approve", self.later("admin", "post", f"/api/purchases/{order['id']}/approve"))
        with hold(purchase_service, "_apply_pending", thread="prepare") as entered:
            results = self.sequenced(prepare, approve, entered)
        self.assertEqual((results["prepare"][0], results["approve"][0]), (200, 200), results)
        self.fresh()
        self.assertEqual(db.session.get(PurchaseOrder, order["id"]).status, "approved")
        self.assertEqual(PurchaseOrder.query.filter_by(supplier_quote_id=order["supplier_quote_id"]).count(), 2)

    def test_legacy_draft_edit_while_approving(self):
        product_id = self.product("LK-LEGACY")
        order = self.draft(product_id)
        self.fresh()
        source = db.session.get(PurchaseOrder, order["id"])
        legacy = PurchaseOrder(supplier_id=source.supplier_id, created_by_user_id=self.users["asistente"],
                               status="pending_approval", currency="VES", supplier_quote_id=source.supplier_quote_id,
                               subtotal=50, total=50)
        legacy.lines = [purchase_service.PurchaseOrderLine(product_id=product_id, product_code="LK-LEGACY",
                                                           product_name="Producto LK-LEGACY", quantity_ordered=5,
                                                           quantity_received=0, unit_cost=10, line_total=50)]
        db.session.add(legacy)
        db.session.flush()
        legacy.order_number = f"OC-{legacy.id:06d}"
        db.session.commit()
        edit = ("edit", self.later("asistente", "patch", f"/api/purchases/{legacy.id}", {
            "supplier_quote_id": order["supplier_quote_id"], "items": [{"product_id": product_id, "quantity": 7}]}))
        approve = ("approve", self.later("admin", "post", f"/api/purchases/{order['id']}/approve"))
        with hold(purchase_service, "_apply_pending", thread="edit") as entered:
            results = self.sequenced_without_deadlock(edit, approve, entered)
        self.assertEqual((results["edit"][0], results["approve"][0]), (200, 200), results)
        self.fresh()
        self.assertEqual(db.session.get(PurchaseOrder, legacy.id).lines[0].quantity_ordered, 7)

    def test_multi_product_quote_in_reverse_order_while_approving(self):
        first, second = self.product("LK-MULTI-A"), self.product("LK-MULTI-B")
        order = self.multi_draft([first, second])
        other = self.second_supplier([first, second])
        quote = ("quote", self.later("asistente", "post", "/api/supplier-quotes", {
            "supplier_id": other, "issued_on": caracas_today(), "currency": "VES", "valid_until": "2099-01-01",
            "items": [{"product_id": key, "quantity": 5, "unit_price": "9.00"} for key in (second, first)]}))
        approve = ("approve", self.later("admin", "post", f"/api/purchases/{order['id']}/approve"))
        with hold(supplier_quote_service, "money", thread="quote") as entered:
            results = self.sequenced_without_deadlock(quote, approve, entered)
        self.assertEqual((results["quote"][0], results["approve"][0]), (201, 200), results)

    def test_multi_product_request_in_reverse_order_while_approving(self):
        first, second = self.product("LK-SR-A"), self.product("LK-SR-B")
        order = self.multi_draft([first, second])
        request = ("request", self.later("inventario", "post", "/api/replenishment-requests",
                                         {"items": [{"product_id": key, "quantity": 3} for key in (second, first)]}))
        approve = ("approve", self.later("admin", "post", f"/api/purchases/{order['id']}/approve"))
        # The first line is built right after its products are locked.
        with hold(replenishment_request_service, "ReplenishmentRequestLine", thread="request") as entered:
            results = self.sequenced_without_deadlock(request, approve, entered)
        self.assertEqual((results["request"][0], results["approve"][0]), (201, 200), results)

    # ---------- shortage freed by a rejected generated order ----------
    def generated(self, product_code):
        original = self.closed_partial(self.product(product_code))
        request = self.ok(201, self.call("inventario", "post", f"/api/replenishment-requests/from-order/{original['id']}"))
        self.ok(200, self.call("asistente", "post", f"/api/replenishment-requests/{request['id']}/start"))
        quote = self.ok(201, self.call("asistente", "post", f"/api/replenishment-requests/{request['id']}/quotes", {
            "supplier_id": self.supplier.id, "currency": "VES", "issued_on": caracas_today(), "valid_until": "2099-01-01",
            "items": [{"product_id": original["items"][0]["product_id"], "unit_price": "12.00"}]}))
        prepared = self.ok(200, self.call("asistente", "post", f"/api/replenishment-requests/{request['id']}/prepare",
                                          {"supplier_quote_id": quote["id"]}))
        return original, prepared["purchase_order"]["id"]

    def test_rejection_holding_the_generated_order_frees_the_shortage(self):
        original, generated = self.generated("SR-REJ-FIRST")
        reject = ("reject", self.later("admin", "post", f"/api/purchases/{generated}/reject", {"reason": "Precio fuera de rango"}))
        again = ("again", self.later("inventario", "post", f"/api/replenishment-requests/from-order/{original['id']}"))
        with hold(purchase_service, "text_value", thread="reject") as entered:
            results = self.sequenced(reject, again, entered)
        self.assertEqual((results["reject"][0], results["again"][0]), (200, 201), results)
        self.assertEqual(self.shortage_count(original["id"]), 2)

    def test_rerequest_holding_the_generated_order_waits_and_refuses(self):
        original, generated = self.generated("SR-REJ-SECOND")
        again = ("again", self.later("inventario", "post", f"/api/replenishment-requests/from-order/{original['id']}"))
        reject = ("reject", self.later("admin", "post", f"/api/purchases/{generated}/reject", {"reason": "Precio fuera de rango"}))
        with hold(replenishment_request_service, "shortage_still_covered", thread="again") as entered:
            results = self.sequenced(again, reject, entered)
        self.assertEqual((results["again"][0], results["reject"][0]), (409, 200), results)
        self.assertEqual(self.shortage_count(original["id"]), 1)
        self.ok(201, self.call("inventario", "post", f"/api/replenishment-requests/from-order/{original['id']}"))
        self.assertEqual(self.shortage_count(original["id"]), 2)

    def test_two_users_rerequest_a_freed_shortage_once(self):
        original, generated = self.generated("SR-REJ-TWO")
        self.ok(200, self.call("admin", "post", f"/api/purchases/{generated}/reject", {"reason": "Precio fuera de rango"}))
        path = f"/api/replenishment-requests/from-order/{original['id']}"
        with hold(replenishment_request_service, "_request_lines") as entered:
            results = self.sequenced(("first", self.later("inventario", "post", path)),
                                     ("other", self.later("inventario2", "post", path)), entered)
        self.assertEqual((results["first"][0], results["other"][0]), (201, 409), results)
        self.assertEqual(self.shortage_count(original["id"]), 2)

    def test_shortage_requests_for_two_orders_at_once(self):
        # Both locking reads gap-lock the end of the source index and both insert there: InnoDB
        # aborts one, the controller retries it, and each order keeps exactly one request.
        first = self.closed_partial(self.product("SR-PAIR-A"))
        second = self.closed_partial(self.product("SR-PAIR-B"))
        with hold(replenishment_request_service, "_request_lines", thread="a") as entered:
            results = self.sequenced(
                ("a", self.later("inventario", "post", f"/api/replenishment-requests/from-order/{first['id']}")),
                ("b", self.later("inventario2", "post", f"/api/replenishment-requests/from-order/{second['id']}")), entered)
        self.assertEqual((results["a"][0], results["b"][0]), (201, 201), results)
        self.assertEqual((self.shortage_count(first["id"]), self.shortage_count(second["id"])), (1, 1))

    # ---------- migration with real links (runs last by name) ----------
    def test_z_migration_rollback_refused_while_links_exist(self):
        order = self.closed_partial(self.product("MIG-LINK"))
        self.ok(201, self.call("inventario", "post", f"/api/replenishment-requests/from-order/{order['id']}"))
        engine = engine_for(DATABASE)
        try:
            with self.assertRaisesRegex(RuntimeError, "Rollback refused"):
                migration.rollback(engine, DATABASE)
            self.assertIn("source_purchase_order_id",
                          {column["name"] for column in inspect(engine).get_columns("replenishment_requests")})
            self.assertEqual(migration.migrate(engine, DATABASE)[0], [])
        finally:
            engine.dispose()
        for target in ("ferreteria_conejo", "ferreteria_conejo_tesis", "conejo_demo"):
            with self.assertRaises(RuntimeError):
                migration.validate_target(target)


if __name__ == "__main__":
    print("Base desechable:", DATABASE)
    unittest.main(verbosity=2)
