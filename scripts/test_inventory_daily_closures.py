"""SQLite desechable y, con --mariadb, copia aislada y protegida de MariaDB.

Nunca admite DEMO ni la base real como destino de escritura. El reloj comercial
se simula exclusivamente aquí; created_at sigue siendo la hora real de captura.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from sqlalchemy import event, inspect, text
from app.extensions import db
from app.models import Category, InventorySnapshot, Product
from app.services import inventory_snapshot_service as snaps, product_history_service as history
from docker_support.scheduler import daily_snapshot_tick

TABLES = (
    "inventory_snapshots", "reconciliation_runs", "reconciliation_lines",
    "reconciliation_cuts", "reconciliation_cut_stock", "purchase_orders",
    "purchase_order_lines", "purchase_closure_requests", "purchase_receptions",
    "purchase_reception_lines", "replenishment_requests", "supplier_quotes",
    "product_state_histories", "delivery_notes", "delivery_note_items",
    "historical_imports", "historical_demand_records",
)
STOCK_QUERIES = {
    "product_stock": "SELECT id,current_stock,minimum_stock,is_active,category_id,approval_status FROM products ORDER BY id",
    "stock_movements": "SELECT id,product_id,movement_type,quantity,previous_stock,new_stock,created_at FROM stock_movements ORDER BY id",
}


def fingerprints(connection):
    inspector = inspect(connection)
    result = {}
    queries = dict(STOCK_QUERIES)
    for table in TABLES:
        keys = inspector.get_pk_constraint(table)["constrained_columns"]
        queries[table] = "SELECT * FROM `" + table + "` ORDER BY " + ",".join("`" + key + "`" for key in keys)
    for name, query in queries.items():
        rows = connection.execute(text(query)).all()
        encoded = json.dumps([list(row) for row in rows], default=str, separators=(",", ":"))
        result[name] = {"count": len(rows), "sha256": hashlib.sha256(encoded.encode()).hexdigest()}
    return result


def fixture_app(uri):
    app = Flask("daily_closure_test", instance_path=tempfile.mkdtemp(prefix="daily-instance-"))
    app.config.update(SQLALCHEMY_DATABASE_URI=uri, SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      INVENTORY_SNAPSHOT_ACTIVATED_AT="", TESTING=True)
    db.init_app(app)
    return app


class DailyClosures(unittest.TestCase):
    def setUp(self):
        self.app = fixture_app("sqlite://")
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        a, b = Category(name="Activa"), Category(name="Inactiva", is_active=False)
        db.session.add_all([a, b])
        db.session.flush()
        db.session.add_all([
            Product(code="D1", name="Disponible", category_id=a.id, current_stock=9, minimum_stock=2),
            Product(code="D2", name="Bajo", category_id=b.id, current_stock=2, minimum_stock=3),
            Product(code="D3", name="Inactivo", category_id=a.id, current_stock=7, minimum_stock=1, is_active=False),
        ])
        db.session.commit()
        self.slot = datetime(2026, 10, 11, 0, 5, tzinfo=snaps.COMMERCIAL_ZONE)
        self.day = self.slot.date() - timedelta(days=1)

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()

    def activate(self):
        self.app.config["INVENTORY_SNAPSHOT_ACTIVATED_AT"] = (self.slot - timedelta(hours=1)).isoformat()

    def test_disabled_even_at_slot(self):
        self.assertEqual(daily_snapshot_tick(self.slot)["reason"], "disabled")
        self.assertEqual(InventorySnapshot.query.count(), 0)

    def test_aware_caracas_boundaries(self):
        self.assertFalse(snaps.capture_slot(self.slot - timedelta(microseconds=1))[2])
        self.assertTrue(snaps.capture_slot(self.slot)[2])
        self.assertTrue(snaps.capture_slot(self.slot + timedelta(seconds=59))[2])
        self.assertFalse(snaps.capture_slot(self.slot + timedelta(minutes=1))[2])
        self.assertEqual(snaps.capture_slot(self.slot.astimezone(timezone.utc))[0], self.slot)
        with self.assertRaises(ValueError):
            snaps.take_snapshot(self.slot.replace(tzinfo=None))

    def test_late_service_cannot_write(self):
        self.assertEqual(snaps.take_snapshot(self.slot + timedelta(hours=10))["reason"], "outside_capture_window")
        self.assertEqual(InventorySnapshot.query.count(), 0)

    def test_activation_after_slot_waits_for_next_day(self):
        self.app.config["INVENTORY_SNAPSHOT_ACTIVATED_AT"] = (self.slot + timedelta(seconds=5)).isoformat()
        self.assertEqual(daily_snapshot_tick(self.slot + timedelta(seconds=10))["reason"], "before_activation")
        result = daily_snapshot_tick(self.slot + timedelta(days=1))
        self.assertEqual(result["snapshot_date"], self.slot.date().isoformat())
        self.assertEqual(result["inserted"], 3)
        self.assertIsNone(history.snapshot_kpis(self.day))

    def test_invalid_activation_fails_closed(self):
        for value in ("hoy", "2026-10-10T20:00:00"):
            self.app.config["INVENTORY_SNAPSHOT_ACTIVATED_AT"] = value
            with self.assertRaises(ValueError):
                daily_snapshot_tick(self.slot)
        self.assertEqual(InventorySnapshot.query.count(), 0)

    def test_actual_capture_fields_and_time(self):
        self.activate()
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        result = daily_snapshot_tick(self.slot)
        after = datetime.now(timezone.utc).replace(tzinfo=None)
        self.assertEqual(result["inserted"], 3)
        rows = InventorySnapshot.query.order_by(InventorySnapshot.product_id).all()
        self.assertEqual([(r.stock, r.minimum_stock, r.product_is_active, r.category_is_active) for r in rows],
                         [(9, 2, True, True), (2, 3, True, False), (7, 1, False, True)])
        self.assertTrue(all(before <= row.created_at <= after for row in rows))
        self.assertTrue(all(row.snapshot_date == self.day for row in rows))
        self.assertEqual(len({row.created_at for row in rows}), 1)

    def test_retry_keeps_original_rows(self):
        snaps.take_snapshot(self.slot)
        original = [(r.id, r.stock, r.created_at) for r in InventorySnapshot.query.all()]
        self.assertTrue(snaps.take_snapshot(self.slot + timedelta(seconds=20))["skipped"])
        self.assertEqual([(r.id, r.stock, r.created_at) for r in InventorySnapshot.query.all()], original)

    def test_restart_uses_database(self):
        self.activate()
        daily_snapshot_tick(self.slot)
        db.session.remove()
        self.assertEqual(daily_snapshot_tick(self.slot + timedelta(seconds=40))["existing"], 3)

    def test_missed_days_never_replayed(self):
        self.activate()
        daily_snapshot_tick(self.slot + timedelta(minutes=5))
        daily_snapshot_tick(self.slot + timedelta(days=3))
        self.assertEqual({r.snapshot_date for r in InventorySnapshot.query.all()}, {self.day + timedelta(days=3)})

    def test_rollback_and_retry(self):
        def fail(*args):
            if args[2].lstrip().upper().startswith("INSERT INTO INVENTORY_SNAPSHOTS"):
                raise RuntimeError("Synthetic insert failure")
        event.listen(db.engine, "after_cursor_execute", fail)
        try:
            with self.assertRaises(RuntimeError):
                snaps.take_snapshot(self.slot)
        finally:
            event.remove(db.engine, "after_cursor_execute", fail)
        self.assertEqual(InventorySnapshot.query.count(), 0)
        self.assertEqual(snaps.take_snapshot(self.slot + timedelta(seconds=20))["inserted"], 3)

    def test_products_reads_saved_close(self):
        snaps.take_snapshot(self.slot)
        result = history.history_summary({"start": str(self.day), "end": str(self.day), "compare": "none"}, self.slot)
        self.assertTrue(result["period"]["available"])
        self.assertEqual(result["kpis"]["total_products"]["value"], 2)
        self.assertEqual(result["kpis"]["low_stock"]["value"], 1)
        self.assertIsNone(history.snapshot_kpis(self.day - timedelta(days=1)))

    def test_empty_catalog_does_not_invent_close(self):
        Product.query.delete()
        db.session.commit()
        self.assertEqual(snaps.take_snapshot(self.slot)["reason"], "empty_catalog")
        self.assertEqual(InventorySnapshot.query.count(), 0)

    def test_window_rechecked_after_lock(self):
        early = snaps.capture_slot(self.slot)
        late = snaps.capture_slot(self.slot + timedelta(minutes=1))
        with patch.object(snaps, "capture_slot", side_effect=[early, late]):
            self.assertEqual(snaps.take_snapshot(self.slot)["reason"], "outside_capture_window")
        self.assertEqual(InventorySnapshot.query.count(), 0)


def guard_mariadb():
    import re
    from docker_support.guard import wait_database
    if os.environ.get("DOCKER_ENV_KIND") != "disposable" or not re.fullmatch(
            r"conejo_disposable_daily_[a-f0-9]{8}", os.environ.get("DB_NAME", "")):
        raise RuntimeError("La suite exige una MariaDB daily desechable, nunca DEMO.")
    expected = os.environ.get("SNAPSHOT_TEST_DB_HOSTNAME", "")
    if not expected:
        raise RuntimeError("Se exige identidad de la MariaDB aislada.")
    wait_database()
    from app.config import Config
    app = fixture_app(Config.SQLALCHEMY_DATABASE_URI)
    with app.app_context(), db.engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT @@hostname").scalar_one() == expected
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "0007_catalog_currency"
    return app


def process_capture(slot, crash=False):
    code = """
import os,time
from datetime import datetime
from sqlalchemy import event
from scripts.test_inventory_daily_closures import guard_mariadb
from app.extensions import db
from docker_support.scheduler import daily_snapshot_tick
app=guard_mariadb()
app.config['INVENTORY_SNAPSHOT_ACTIVATED_AT']=os.environ['TEST_ACTIVATION']
with app.app_context():
    if os.environ.get('TEST_CRASH') == '1':
        def pause(c,cur,statement,*args):
            if statement.lstrip().upper().startswith('INSERT INTO INVENTORY_SNAPSHOTS'):
                print('UNCOMMITTED',flush=True)
                time.sleep(30)
        event.listen(db.engine,'after_cursor_execute',pause)
    print(daily_snapshot_tick(datetime.fromisoformat(os.environ['TEST_SLOT'])),flush=True)
"""
    values = {**os.environ, "TEST_SLOT": slot.isoformat(),
              "TEST_ACTIVATION": (slot - timedelta(hours=1)).isoformat(), "TEST_CRASH": "1" if crash else "0"}
    if not crash:
        return subprocess.run([sys.executable, "-c", code], env=values, check=True, capture_output=True, text=True).stdout
    child = subprocess.Popen([sys.executable, "-c", code], env=values, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "UNCOMMITTED"
    finally:
        child.kill()
        child.wait(timeout=10)


def check_atomic_rollback(slot):
    # Failure after the insert must roll back every row and release the lock.
    def fail(c, cur, statement, *args):
        if statement.lstrip().upper().startswith("INSERT INTO INVENTORY_SNAPSHOTS"):
            raise RuntimeError("Synthetic rollback")
    event.listen(db.engine, "after_cursor_execute", fail)
    try:
        with unittest.TestCase().assertRaises(RuntimeError):
            daily_snapshot_tick(slot)
    finally:
        event.remove(db.engine, "after_cursor_execute", fail)
    assert InventorySnapshot.query.count() == 0


def check_process_recovery(slot, stock):
    db.session.remove()
    process_capture(slot, crash=True)
    process_capture(slot)
    original = [(r.id, r.stock, r.created_at) for r in InventorySnapshot.query.order_by(InventorySnapshot.id)]
    process_capture(slot)
    db.session.remove()
    assert original == [(r.id, r.stock, r.created_at) for r in InventorySnapshot.query.order_by(InventorySnapshot.id)]
    rows = InventorySnapshot.query.order_by(InventorySnapshot.product_id).all()
    assert [(r.product_id,r.stock,r.minimum_stock,r.product_is_active,r.category_id,r.category_is_active) for r in rows] == [tuple(r) for r in stock]
    assert len({r.created_at for r in rows}) == 1
    assert all(abs((datetime.now(timezone.utc).replace(tzinfo=None)-r.created_at).total_seconds()) < 120 for r in rows)


def check_concurrent_capture(app, slot, stock):
    next_slot = slot + timedelta(days=1)
    def attempt(_):
        with app.app_context():
            return daily_snapshot_tick(next_slot)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(attempt, range(6)))
    assert sum(r["inserted"] for r in results) == len(stock)
    db.session.remove()  # End the observer's earlier REPEATABLE READ view.
    assert InventorySnapshot.query.filter_by(snapshot_date=next_slot.date()-timedelta(days=1)).count() == len(stock)


def check_consistent_view(slot, stock):
    # Live catalogue changes after the first consistent read: capture must see the earlier view.
    pid, initial = stock[0][:2]
    changed = False
    def change(c, cur, statement, *args):
        nonlocal changed
        if not changed and statement.lstrip().upper().startswith("SELECT COUNT("):
            changed = True
            with db.engine.begin() as writer:
                writer.execute(text("UPDATE products SET current_stock=current_stock+1 WHERE id=:id"), {"id":pid})
    event.listen(db.engine, "after_cursor_execute", change)
    try:
        daily_snapshot_tick(slot + timedelta(days=2))
    finally:
        event.remove(db.engine, "after_cursor_execute", change)
        with db.engine.begin() as writer:
            writer.execute(text("UPDATE products SET current_stock=:stock WHERE id=:id"), {"id":pid,"stock":initial})
    db.session.remove()
    assert changed
    assert InventorySnapshot.query.filter_by(product_id=pid,snapshot_date=slot.date()+timedelta(days=1)).one().stock == initial


def check_readers(app, slot):
    # Exercise the real, decorated GET views with read-only SQL enforced.
    from app import create_app
    from app.config import Config
    from app.models import User
    from flask_login import login_user
    web = create_app(Config)
    web.instance_path = app.instance_path
    def no_writes(c, cur, statement, *args):
        assert statement.lstrip().split()[0].upper() in {"SELECT","SHOW","DESCRIBE"}, "GET attempted a write"
    with web.app_context(), web.test_request_context():
        login_user(User.query.filter_by(role="admin").first())
        event.listen(db.engine, "before_cursor_execute", no_writes)
        try:
            with patch("app.services.inventory_summary_service.commercial_today", return_value=slot.date()):
                response = web.view_functions["inventory.inventory_summary"]()
                body = response.get_json()
                assert body["kpis"]["products_in_stock"]["comparison"]["available"]
            day = slot.date()-timedelta(days=1)
            with web.test_request_context(query_string={"start":str(day),"end":str(day),"compare":"none"}):
                login_user(User.query.filter_by(role="admin").first())
                with patch("app.services.product_history_service.commercial_today", return_value=slot.date()):
                    response = web.view_functions["products.products_history_summary"]()
                    assert response.get_json()["history"]["current_period_available"]
        finally:
            event.remove(db.engine, "before_cursor_execute", no_writes)
            db.session.remove()
            db.engine.dispose()


def check_worker_loop(slot, stock):
    """Start the packaged worker loop, interrupt it, then verify durable identity."""
    code = """
import os
from datetime import datetime
from scripts.test_inventory_daily_closures import guard_mariadb
guard_mariadb()
from app.services import inventory_snapshot_service as s
original=s.capture_slot
clock=datetime.fromisoformat(os.environ['TEST_SLOT'])
s.capture_slot=lambda now=None: original(clock)
from docker_support.scheduler import main
main()
"""
    values = {**os.environ, "TEST_SLOT": slot.isoformat(),
              "INVENTORY_SNAPSHOT_ACTIVATED_AT": (slot-timedelta(hours=1)).isoformat()}
    child = subprocess.Popen([sys.executable,"-u","-c",code],env=values,
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        with ThreadPoolExecutor(max_workers=1) as reader:
            line = reader.submit(child.stdout.readline).result(timeout=20)
        assert line.startswith("Daily close saved:"), "Packaged worker failed to save its close"
    finally:
        child.kill()
        child.wait(timeout=10)
    db.session.remove()
    assert InventorySnapshot.query.filter_by(snapshot_date=slot.date()-timedelta(days=1)).count() == len(stock)
    process_capture(slot)
    db.session.remove()
    assert InventorySnapshot.query.filter_by(snapshot_date=slot.date()-timedelta(days=1)).count() == len(stock)


def maria_demonstration():
    app = guard_mariadb()
    slot = datetime.combine(snaps.commercial_today() + timedelta(days=1), snaps.CLOSING_TIME, snaps.COMMERCIAL_ZONE)
    app.config["INVENTORY_SNAPSHOT_ACTIVATED_AT"] = (slot - timedelta(hours=1)).isoformat()
    checks = []
    with app.app_context():
        with db.engine.connect() as c:
            before = fingerprints(c)
            assert before["inventory_snapshots"]["count"] == 0, "Requires fresh restored copy without closes"
            schema_before = {t: c.exec_driver_sql("SHOW CREATE TABLE `" + t + "`").one()[1]
                             for t in inspect(c).get_table_names()}
            stock = c.exec_driver_sql("SELECT p.id,p.current_stock,p.minimum_stock,p.is_active,p.category_id,c.is_active "
                                      "FROM products p JOIN categories c ON c.id=p.category_id ORDER BY p.id").all()
        check_atomic_rollback(slot)
        checks.append("atomic rollback after insert")
        check_process_recovery(slot, stock)
        checks.append("process killed before commit; new process recovers; restart after commit unchanged")
        checks.append("authentic stock, inactive flags and actual UTC timestamps")
        check_concurrent_capture(app, slot, stock)
        checks.append("six concurrent captures produce one complete close")
        check_consistent_view(slot, stock)
        checks.append("REPEATABLE READ view under concurrent stock change; original stock restored")
        # Restart too late: no catch-up; even the direct service refuses.
        assert daily_snapshot_tick(slot + timedelta(days=4, hours=2))["reason"] == "outside_capture_window"
        assert snaps.take_snapshot(slot + timedelta(days=4, hours=2))["reason"] == "outside_capture_window"
        assert history.snapshot_kpis(slot.date()+timedelta(days=3)) is None
        checks.append("missed days remain unavailable; no historical reconstruction")
        check_readers(app, slot)
        checks.append("Inventory Summary and Products decorated GETs read saved closes without writes")
        check_worker_loop(slot+timedelta(days=3), stock)
        checks.append("packaged worker main loop saves close; process interruption and restart stay idempotent")
        with db.engine.connect() as c:
            after = fingerprints(c)
            schema_after = {t: c.exec_driver_sql("SHOW CREATE TABLE `" + t + "`").one()[1]
                            for t in inspect(c).get_table_names()}
        assert {k:v for k,v in before.items() if k != "inventory_snapshots"} == {k:v for k,v in after.items() if k != "inventory_snapshots"}
        # SHOW CREATE TABLE includes AUTO_INCREMENT; only snapshots advance it.
        assert {k:v for k,v in schema_before.items() if k != "inventory_snapshots"} == {k:v for k,v in schema_after.items() if k != "inventory_snapshots"}
        assert after["inventory_snapshots"]["count"] == len(stock)*4
        checks.append("18 preserved fingerprints identical; only daily snapshots added; reconciliation stays separate")
        result = {"isolated":True,"checks":checks,"before":before,"after":after,"products_per_close":len(stock)}
        print(json.dumps(result, default=str))
        db.session.remove()
        db.engine.dispose()


if __name__ == "__main__":
    if sys.argv[1:] == ["--mariadb"]:
        maria_demonstration()
    else:
        unittest.main()
