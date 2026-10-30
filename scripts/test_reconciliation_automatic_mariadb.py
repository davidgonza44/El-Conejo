"""Opt-in MariaDB integration test; creates and drops ONLY its own random loopback database.

Run only on an explicitly disposable local MariaDB server with
RUN_RECONCILIATION_MARIADB_TESTS=1 and TEST_MARIADB_USER set. Never points at tesis.
"""
import os
import secrets
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.models import Category, Product, StockMovement
from app.models.reconciliation import ReconciliationCut, ReconciliationCutStock, ReconciliationRun
from app.services import reconciliation_automation_service as auto
from migrate_reconciliation_cuts import validate_target


def _guard():
    if os.getenv("RUN_RECONCILIATION_MARIADB_TESTS") != "1" or not os.getenv("TEST_MARIADB_USER"):
        raise RuntimeError("Explicit disposable MariaDB opt-in and TEST_MARIADB_USER required.")
    if os.getenv("TEST_MARIADB_HOST", "127.0.0.1") not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("Disposable test must use loopback.")
    directory = Path(os.environ.get("TEST_MARIADB_DATADIR", "")).resolve()
    if (directory.parent.parent != Path(__file__).resolve().parents[1] / "tmp"
            or not directory.parent.name.startswith("reconciliation-server-") or directory.name != "data"
            or int(os.getenv("TEST_MARIADB_PORT", "3306")) == 3306):
        raise RuntimeError("A separate disposable server datadir and non-operational port are required.")


def _url(name=None):
    return URL.create("mysql+pymysql", username=os.environ["TEST_MARIADB_USER"],
                      password=os.getenv("TEST_MARIADB_PASSWORD", ""), host="127.0.0.1",
                      port=int(os.getenv("TEST_MARIADB_PORT", "3306")), database=name,
                      query={"charset": "utf8mb4"})


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def _view_contract_test():
    """Demonstrate why START alone at READ COMMITTED is insufficient for two reads."""
    for level in ("READ COMMITTED", "REPEATABLE READ"):
        with db.engine.connect().execution_options(isolation_level=level) as snapshot:
            snapshot.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            before = snapshot.exec_driver_sql("SELECT current_stock FROM products ORDER BY id").scalars().all()
            with db.engine.begin() as writer:
                writer.exec_driver_sql("UPDATE products SET current_stock=current_stock+1")
            after = snapshot.exec_driver_sql("SELECT current_stock FROM products ORDER BY id").scalars().all()
            _assert(snapshot.exec_driver_sql("SELECT @@in_transaction").scalar() == 1, "No real transaction")
            _assert(snapshot.exec_driver_sql("SELECT @@autocommit").scalar() == 0, "Autocommit enabled")
            expected = before if level == "REPEATABLE READ" else [value + 1 for value in before]
            _assert(after == expected, "Unexpected real MariaDB multi-SELECT behavior")
            snapshot.rollback()
        with db.engine.begin() as writer:
            writer.exec_driver_sql("UPDATE products SET current_stock=current_stock-1")
        print("PASS: real multi-SELECT", level, "stable=" + str(before == after))


def _timing_test(app):
    real_now = datetime.utcnow().replace(microsecond=0)
    for offset, expected in ((-1, "pending"), (119, "captured"), (120, "unavailable")):
        when = real_now - timedelta(seconds=offset)
        with patch.object(auto, "scheduled_at", return_value=when), patch.object(auto, "datetime") as clock:
            clock.utcnow.return_value = real_now
            cut = auto.capture_cut(date.today(), "0800")
        _assert(cut.snapshot_status == expected, "Incorrect two-minute capture boundary")
        if expected != "captured":
            _assert(not cut.stock, "Early/missed cut captured stock")
    with db.engine.connect() as connection:
        _assert(connection.exec_driver_sql("SELECT @@tx_isolation").scalar() == "READ-COMMITTED",
                "Local capture isolation leaked into application pool")


def _product_snapshot_test(app):
    with app.app_context():
        snapshot_time = datetime.utcnow().replace(microsecond=0) - timedelta(seconds=10)
        with patch.object(auto, "scheduled_at", return_value=snapshot_time):
            cut = auto.capture_cut(date.today(), "0800")
            _assert(cut.snapshot_status == "captured", "Cut was not captured")
            _assert([(row.product_code_snapshot, row.quantity) for row in cut.stock] == [("TEST-01", 10), ("TEST-02", 20)],
                    "Initial snapshot differs from committed stock")
            Product.query.filter_by(code="TEST-01").one().current_stock = 13
            db.session.commit()
            again = auto.capture_cut(date.today(), "0800")
            _assert(again.id == cut.id and again.stock[0].quantity == 10, "Snapshot changed on replay")
            inbox = Path(app.instance_path) / "reconciliation_inbox"
            inbox.mkdir()
            name = "inventario_2026-09-30_0800.csv"
            temporary = inbox / (name + ".tmp")
            temporary.write_bytes(b"product_code;quantity\nTEST-01;14\n")
            with patch.object(auto, "filename_for", return_value=name):
                _assert(auto.process_cut(cut.id).run_id is None, "Temporary file was ingested")
            temporary.rename(inbox / name)
            with patch.object(auto, "filename_for", return_value=name):
                result = auto.process_cut(cut.id)
            _assert(result.status == "completed" and result.run.lines[0].system_quantity == 10,
                    "Late CSV did not use frozen quantity")
            _assert(result.run.lines[0].difference == 4, "Frozen difference incorrect")
            _assert(Product.query.filter_by(code="TEST-01").one().current_stock == 13,
                    "Automatic comparison changed operational stock")
            _assert(StockMovement.query.count() == 0, "Automatic comparison created a movement")
            duplicate = ReconciliationCut(source=cut.source, scheduled_for=cut.scheduled_for)
            db.session.add(duplicate)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
            else:
                raise AssertionError("UNIQUE(source, scheduled_for) did not reject a duplicate")


def _concurrent_move_test(app):
    started, updated = Event(), Event()
    snapshot_time = datetime.utcnow().replace(microsecond=0) - timedelta(seconds=30)

    def pause_after_start(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.upper().startswith("START TRANSACTION WITH CONSISTENT SNAPSHOT"):
            _assert(_conn.exec_driver_sql("SELECT @@tx_isolation").scalar() == "REPEATABLE-READ", "Capture isolation")
            first = _conn.exec_driver_sql("SELECT current_stock FROM products ORDER BY id").scalars().all()
            started.set()
            _assert(updated.wait(10), "Concurrent stock move did not finish")
            second = _conn.exec_driver_sql("SELECT current_stock FROM products ORDER BY id").scalars().all()
            _assert(first == second, "Capture's multi-SELECT view changed during concurrent update")

    def capture():
        with app.app_context():
            return auto.capture_cut(date.today(), "1200").id

    def move():
        _assert(started.wait(10), "Snapshot did not start")
        with app.app_context():
            Product.query.filter_by(code="TEST-01").one().current_stock = 17
            Product.query.filter_by(code="TEST-02").one().current_stock = 27
            db.session.commit()
        updated.set()

    event.listen(db.engine, "after_cursor_execute", pause_after_start)
    try:
        with patch.object(auto, "scheduled_at", return_value=snapshot_time), ThreadPoolExecutor(max_workers=2) as pool:
            capture_job, move_job = pool.submit(capture), pool.submit(move)
            cut_id = capture_job.result(timeout=20)
            move_job.result(timeout=20)
    finally:
        event.remove(db.engine, "after_cursor_execute", pause_after_start)
    cut = db.session.get(ReconciliationCut, cut_id)
    _assert(cut.stock[0].quantity == 13, "Consistent snapshot saw a later committed movement")
    _assert(cut.stock[1].quantity == 20, "Second product used a different snapshot")
    _assert(Product.query.filter_by(code="TEST-01").one().current_stock == 17, "Capture overwrote concurrent stock")
    _assert(Product.query.filter_by(code="TEST-02").one().current_stock == 27, "Capture overwrote second product")
    _assert(StockMovement.query.count() == 0, "Capture created an operational movement")


def _concurrent_capture_and_rollback_test(app):
    snapshot_time = datetime.utcnow().replace(microsecond=0) - timedelta(seconds=60)
    first_started, second_returned = Event(), Event()

    def pause_first(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.upper().startswith("START TRANSACTION WITH CONSISTENT SNAPSHOT"):
            first_started.set()
            _assert(second_returned.wait(10), "Second capturer did not yield to the advisory lock")

    def capture(index):
        if index == 1:
            _assert(first_started.wait(10), "First capture did not hold the snapshot lock")
        with app.app_context():
            result = auto.capture_cut(date.today(), "1600").id
        if index == 1:
            second_returned.set()
        return result

    event.listen(db.engine, "after_cursor_execute", pause_first)
    try:
        with patch.object(auto, "scheduled_at", return_value=snapshot_time), ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(capture, range(2)))
    finally:
        event.remove(db.engine, "after_cursor_execute", pause_first)
    _assert(ids[0] == ids[1], "Two captures created different cuts")
    _assert(ReconciliationCutStock.query.filter_by(cut_id=ids[0]).count() == 2,
            "Concurrent capture duplicated snapshot rows")

    def fail_insert(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.upper().startswith("INSERT INTO RECONCILIATION_CUT_STOCK"):
            cut_id = _context.compiled_parameters[0]["cut_id"]
            with db.engine.connect() as observer:
                visible = observer.execute(text("SELECT COUNT(*) FROM reconciliation_cut_stock WHERE cut_id=:id"),
                                           {"id": cut_id}).scalar()
                _assert(visible == 0, "A reader observed an uncommitted/partial snapshot")
            raise RuntimeError("Synthetic snapshot insert failure")

    # Fail AFTER INSERT executes, so rollback must remove actual uncommitted rows.
    event.listen(db.engine, "after_cursor_execute", fail_insert)
    try:
        with patch.object(auto, "scheduled_at", return_value=snapshot_time + timedelta(seconds=1)):
            try:
                auto.capture_cut(date.today(), "1830")
            except RuntimeError as error:
                _assert("Synthetic" in str(error), "Unexpected capture failure")
            else:
                raise AssertionError("Mid-snapshot failure did not propagate")
    finally:
        event.remove(db.engine, "after_cursor_execute", fail_insert)
    failed_cut = ReconciliationCut.query.filter_by(scheduled_for=snapshot_time + timedelta(seconds=1)).one()
    _assert(failed_cut.snapshot_status == "pending" and not failed_cut.stock,
            "Rollback left a partial or false-complete snapshot")
    _assert(Product.query.filter_by(code="TEST-01").one().current_stock == 17, "Failed capture changed stock")


def main():
    _guard()
    name = "reconciliation_test_" + secrets.token_hex(6)
    validate_target(name)
    server = create_engine(_url())
    created = False
    try:
        with server.begin() as connection:
            _assert(Path(connection.execute(text("SELECT @@datadir")).scalar()).resolve()
                    == Path(os.environ["TEST_MARIADB_DATADIR"]).resolve(), "Wrong server datadir")
            _assert(connection.dialect.is_mariadb and "MariaDB" in connection.execute(text("SELECT VERSION()")).scalar(),
                    "A real MariaDB server is required")
            _assert(connection.execute(text("SELECT DATABASE()")).scalar() is None,
                    "Server connection must not select an existing database")
            connection.execute(text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4"))
            created = True

        class DisposableConfig:
            TESTING = True
            SECRET_KEY = "disposable-reconciliation"
            SQLALCHEMY_DATABASE_URI = _url(name)
            # Deliberately exercise an app whose default is NOT sufficient for snapshots.
            SQLALCHEMY_ENGINE_OPTIONS = {"isolation_level": "READ COMMITTED"}
            SQLALCHEMY_TRACK_MODIFICATIONS = False

        app = create_app(DisposableConfig)
        folder = tempfile.TemporaryDirectory(prefix="reconciliation-disposable-")
        try:
            app.instance_path = folder.name
            app.config.update(RECONCILIATION_INBOX_PATH="reconciliation_inbox",
                              RECONCILIATION_ARCHIVE_PATH="reconciliation_archive",
                              RECONCILIATION_ACTIVATED_ON=date.today().isoformat())
            with app.app_context():
                db.create_all()
                with db.engine.connect() as connection:
                    print("SERVER:", connection.exec_driver_sql("SELECT VERSION(), @@tx_isolation, @@autocommit").one())
                    print("PRODUCTS ENGINE:", connection.exec_driver_sql("SELECT ENGINE FROM information_schema.TABLES "
                          "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='products'").scalar())
                with db.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
                    auto._verify_mariadb(connection)
                category = Category(name="Disposable")
                db.session.add(category)
                db.session.flush()
                db.session.add(Product(code="TEST-01", name="Disposable", category_id=category.id,
                                       current_stock=10, purchase_price=1, sale_price=2))
                db.session.add(Product(code="TEST-02", name="Disposable second", category_id=category.id,
                                       current_stock=20, purchase_price=1, sale_price=2))
                db.session.commit()
                _view_contract_test()
                _product_snapshot_test(app)
                _concurrent_move_test(app)
                _concurrent_capture_and_rollback_test(app)
                _timing_test(app)
                db.session.remove()
                db.engine.dispose()
        finally:
            folder.cleanup()
    finally:
        if created:
            validate_target(name)
            with server.begin() as connection:
                _assert(connection.execute(text("SELECT DATABASE()")).scalar() is None,
                        "Refusing cleanup from a selected database")
                connection.execute(text(f"DROP DATABASE `{name}`"))
        server.dispose()
    print("PASS: real MariaDB snapshot, concurrency, idempotency, rollback, and frozen CSV comparison")


if __name__ == "__main__":
    main()
