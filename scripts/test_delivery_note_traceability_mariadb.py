"""Delivery-note links and cancel lock on a new loopback database only.

Never selects ferreteria_conejo_tesis. SQLite is not used for the row lock.
"""
import os
import secrets
import sys
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError

from migrate_delivery_note_traceability import engine_for, migrate, validate_target

HOST = "127.0.0.1"
PORT = 3306
USER = os.environ.get("DB_USER", "root")
PASSWORD = os.environ.get("DB_PASSWORD", "")
FORBIDDEN = "ferreteria_conejo_tesis"


def fresh_name():
    name = "delivery_note_traceability_test_" + secrets.token_hex(6)
    validate_target(name)
    if FORBIDDEN in name or "tesis" in name:
        raise RuntimeError("Disposable name collided with a protected database.")
    return name


def guards():
    for name in (FORBIDDEN, "ferreteria_conejo", "ferreteria_conejo_e2e", "mysql", ""):
        try:
            validate_target(name)
        except RuntimeError:
            continue
        raise AssertionError("Guard accepted " + name)
    engine = Mock()
    engine.url.database = FORBIDDEN
    try:
        migrate(engine, FORBIDDEN)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Migration started against the thesis database.")
    engine.begin.assert_not_called()
    try:
        engine_for(fresh_name(), "example.com", PORT, USER)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Non-loopback server was accepted.")
    print("PASS: thesis, valuable names and non-loopback hosts are rejected before connect.")


def server_engine():
    if HOST not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("Only loopback disposable servers are allowed.")
    return create_engine(URL.create("mysql+pymysql", username=USER, password=PASSWORD,
                                     host=HOST, port=PORT, query={"charset": "utf8mb4"}))


def create_database(server, name):
    validate_target(name)
    with server.begin() as connection:
        current = connection.execute(text("SELECT DATABASE()")).scalar()
        if current == FORBIDDEN:
            raise RuntimeError("Server connection selected the thesis database.")
        connection.execute(text(
            f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
    return True


def drop_database(server, name):
    if not name:
        return
    validate_target(name)
    with server.begin() as connection:
        current = connection.execute(text("SELECT DATABASE()")).scalar()
        if current == FORBIDDEN:
            raise RuntimeError("Refusing to drop while the thesis database is selected.")
        connection.execute(text(f"DROP DATABASE `{name}`"))


def _assert_database(connection, name):
    if connection.execute(text("SELECT DATABASE()")).scalar() != name:
        raise RuntimeError("Connected database is not the disposable target.")


def _base_tables(connection):
    connection.execute(text(
        "CREATE TABLE stock_movements (id INT NOT NULL PRIMARY KEY) ENGINE=InnoDB"))
    connection.execute(text(
        "CREATE TABLE delivery_note_items (id INT NOT NULL PRIMARY KEY, "
        "delivery_note_id INT NOT NULL, product_id INT NOT NULL, "
        "quantity DECIMAL(12,2) NOT NULL, marker VARCHAR(40) NOT NULL) ENGINE=InnoDB"))


def _row(connection):
    return connection.execute(text(
        "SELECT id, delivery_note_id, product_id, quantity, marker FROM delivery_note_items"
    )).all()


def migration_cases(server):
    idempotent = fresh_name()
    negative = fresh_name()
    created = []
    try:
        create_database(server, idempotent)
        created.append(idempotent)
        engine = engine_for(idempotent, HOST, PORT, USER, PASSWORD)
        with engine.begin() as connection:
            _assert_database(connection, idempotent)
            _base_tables(connection)
            connection.execute(text(
                "INSERT INTO stock_movements (id) VALUES (7)"))
            connection.execute(text(
                "INSERT INTO delivery_note_items "
                "(id, delivery_note_id, product_id, quantity, marker) "
                "VALUES (1, 1, 1, 2.00, 'historical-row')"))
            before = _row(connection)
            movements = connection.execute(text("SELECT id FROM stock_movements")).all()
        migrate(engine, idempotent)
        migrate(engine, idempotent)
        with engine.begin() as connection:
            _assert_database(connection, idempotent)
            if _row(connection) != before:
                raise AssertionError("Migration changed a pre-existing delivery-note row.")
            if connection.execute(text("SELECT id FROM stock_movements")).all() != movements:
                raise AssertionError("Migration changed a pre-existing stock movement.")
            links = connection.execute(text(
                "SELECT issue_stock_movement_id, reversal_stock_movement_id "
                "FROM delivery_note_items WHERE id = 1")).one()
            if links != (None, None):
                raise AssertionError("Historical movement links were backfilled.")
        engine.dispose()
        print("PASS: two migrations; historical rows unchanged; both links NULL.")

        create_database(server, negative)
        created.append(negative)
        bad = engine_for(negative, HOST, PORT, USER, PASSWORD)
        with bad.begin() as connection:
            _assert_database(connection, negative)
            connection.execute(text(
                "CREATE TABLE stock_movements (id INT NOT NULL PRIMARY KEY) ENGINE=InnoDB"))
            connection.execute(text(
                "CREATE TABLE delivery_note_items (id INT NOT NULL PRIMARY KEY, "
                "marker VARCHAR(20) NOT NULL) ENGINE=MyISAM"))
        try:
            migrate(bad, negative)
        except RuntimeError:
            pass
        else:
            raise AssertionError("MyISAM schema was accepted.")
        with bad.begin() as connection:
            connection.execute(text("DROP TABLE delivery_note_items"))
            connection.execute(text(
                "CREATE TABLE delivery_note_items (id INT NOT NULL PRIMARY KEY, "
                "marker VARCHAR(20) NOT NULL, issue_stock_movement_id VARCHAR(20) NULL) "
                "ENGINE=InnoDB"))
            connection.execute(text(
                "INSERT INTO delivery_note_items (id, marker) VALUES (1, 'keep')"))
        try:
            migrate(bad, negative)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Incompatible column type was repaired automatically.")
        with bad.begin() as connection:
            kind = connection.execute(text(
                "SELECT DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = 'delivery_note_items' AND COLUMN_NAME = 'issue_stock_movement_id'"
            )).scalar()
            other = connection.execute(text(
                "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = 'delivery_note_items' AND COLUMN_NAME = 'reversal_stock_movement_id'"
            )).scalar()
            marker = connection.execute(text("SELECT marker FROM delivery_note_items WHERE id = 1")).scalar()
            if kind != "varchar" or other != 0 or marker != "keep":
                raise AssertionError("Incompatible schema was altered.")
            connection.execute(text("DROP TABLE delivery_note_items"))
            connection.execute(text(
                "CREATE TABLE delivery_note_items (id INT NOT NULL PRIMARY KEY, "
                "marker VARCHAR(20) NOT NULL, issue_stock_movement_id INT NULL) ENGINE=InnoDB"))
            connection.execute(text(
                "INSERT INTO delivery_note_items (id, marker) VALUES (1, 'partial')"))
        try:
            migrate(bad, negative)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Partial movement-link schema was accepted.")
        with bad.begin() as connection:
            other = connection.execute(text(
                "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = 'delivery_note_items' AND COLUMN_NAME = 'reversal_stock_movement_id'"
            )).scalar()
            marker = connection.execute(text("SELECT marker FROM delivery_note_items WHERE id = 1")).scalar()
            if other != 0 or marker != "partial":
                raise AssertionError("Partial schema was altered.")
        bad.dispose()
        print("PASS: MyISAM, wrong column type and partial links are rejected without repair.")
    finally:
        for name in created:
            drop_database(server, name)


def _client(app, user_id):
    from flask import session

    from app.extensions import db
    from app.models import User
    from app.services import session_service

    client = app.test_client()
    with app.app_context():
        with app.test_request_context():
            session_service.open_session(db.session.get(User, user_id))
            cookies = dict(session)
        db.session.remove()
    with client.session_transaction() as stored:
        stored.update(cookies)
    return client


def concurrency_case(server):
    name = fresh_name()
    create_database(server, name)
    application = None
    try:
        import sqlalchemy

        from app import create_app
        from app.extensions import db
        from app.models import Category, Product, User
        from app.services import rbac_service

        uri = URL.create("mysql+pymysql", username=USER, password=PASSWORD, host=HOST,
                         port=PORT, database=name, query={"charset": "utf8mb4"}).render_as_string(hide_password=False)
        if FORBIDDEN in uri:
            raise RuntimeError("Application URI names the thesis database.")

        class DisposableConfig:
            TESTING = True
            SECRET_KEY = "disposable-delivery-note-traceability"
            APP_ENV = "testing"
            SQLALCHEMY_DATABASE_URI = uri
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            SQLALCHEMY_ENGINE_OPTIONS = {"pool_size": 4, "max_overflow": 0}
            WTF_CSRF_ENABLED = False
            MAIL_ENABLED = False
            NOTIFICATIONS_DRY_RUN = True

        application = create_app(DisposableConfig)
        connection_ids = []

        def remember(dbapi_connection, _record, _proxy):
            cursor = dbapi_connection.cursor()
            cursor.execute("SELECT DATABASE()")
            selected = cursor.fetchone()[0]
            cursor.close()
            if selected != name:
                raise RuntimeError("Application checked out a connection to another database.")
            connection_ids.append(dbapi_connection.thread_id())

        with application.app_context():
            if db.engine.url.database != name:
                raise RuntimeError("Application engine is not the disposable database.")
            sqlalchemy.event.listen(db.engine, "checkout", remember)
            db.create_all()
            rbac_service.ensure_seeded()
            category = Category(name="Nota desechable")
            db.session.add(category)
            db.session.flush()
            user = User(name="Admin desechable", username="dn-lock", email="dn-lock@example.invalid", role="admin")
            first = Product(code="DN-LOCK-1", name="Producto uno", category_id=category.id,
                            current_stock=10, minimum_stock=0, purchase_price=1, sale_price=2, is_active=True)
            second = Product(code="DN-LOCK-2", name="Producto dos", category_id=category.id,
                             current_stock=20, minimum_stock=0, purchase_price=1, sale_price=2, is_active=True)
            db.session.add_all([user, first, second])
            db.session.commit()
            user_id, product_ids = user.id, [first.id, second.id]
            first_price, second_price = first.sale_price, second.sale_price
            db.session.remove()

        issuer = _client(application, user_id)
        created = issuer.post("/api/delivery-notes", json={
            "customer_name": "Cliente desechable",
            "items": [{"product_id": product_ids[0], "quantity": 2, "expected_unit_price": f"{first_price:.2f}"},
                      {"product_id": product_ids[1], "quantity": 3, "expected_unit_price": f"{second_price:.2f}"}],
        })
        if created.status_code != 201:
            raise AssertionError(created.get_json())
        note_id = created.get_json()["delivery_note"]["id"]
        workers = [_client(application, user_id), _client(application, user_id)]
        connection_ids.clear()
        barrier = Barrier(2)

        def cancel(client):
            barrier.wait(10)
            return client.post(f"/api/delivery-notes/{note_id}/cancel", json={"reason": "Cancelación de prueba"}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(cancel, workers))
        if statuses != [200, 409]:
            raise AssertionError("Concurrent cancel statuses were " + str(statuses))
        if len(set(connection_ids)) < 2:
            raise AssertionError("Both cancels used the same database connection.")
        with application.app_context():
            db.session.remove()
            counts = dict(db.session.execute(text(
                "SELECT movement_type, COUNT(*) FROM stock_movements GROUP BY movement_type")).all())
            links = db.session.execute(text(
                "SELECT issue_stock_movement_id, reversal_stock_movement_id "
                "FROM delivery_note_items WHERE delivery_note_id = :note"), {"note": note_id}).all()
            stocks = {row[0]: row[1] for row in db.session.execute(text(
                "SELECT id, current_stock FROM products")).all()}
            status = db.session.execute(text(
                "SELECT status FROM delivery_notes WHERE id = :note"), {"note": note_id}).scalar()
            if counts.get("salida") != 2 or counts.get("entrada") != 2:
                raise AssertionError("Movement counts were " + str(counts))
            if status != "cancelled" or len(links) != 2:
                raise AssertionError("Note was not cancelled exactly once.")
            if any(issue is None or reversal is None or issue == reversal for issue, reversal in links):
                raise AssertionError("Issue and reversal links were " + str(links))
            if len({reversal for _, reversal in links}) != 2:
                raise AssertionError("Reversal links were not one per item.")
            if stocks != {product_ids[0]: 10, product_ids[1]: 20}:
                raise AssertionError("Stock after one reversal was " + str(stocks))
            db.session.remove()
        print("PASS: two connections; statuses [200, 409]; one reversal per item; stock restored.")
    finally:
        if application is not None:
            with application.app_context():
                from app.extensions import db
                db.session.remove()
                db.engine.dispose()
        drop_database(server, name)


def main():
    guards()
    server = server_engine()
    try:
        with server.connect() as connection:
            current = connection.execute(text("SELECT DATABASE()")).scalar()
            if current == FORBIDDEN:
                raise RuntimeError("Initial server connection selected the thesis database.")
            version = connection.execute(text("SELECT VERSION()")).scalar()
        print("Server: " + str(version) + " at loopback, no database selected.")
        migration_cases(server)
        concurrency_case(server)
    except OperationalError as exc:
        raise SystemExit("Disposable MariaDB is not reachable on loopback: " + exc.__class__.__name__) from exc
    finally:
        server.dispose()


if __name__ == "__main__":
    main()
