"""CLI-only ownership registry and fail-closed transaction boundary for thesis data."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
import socket
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from seed_tesis_data import SEED, START, END

DATABASE = "ferreteria_conejo_tesis"
DATASET = "tesis-synthetic-v2"
VERSION = 3
LOCK = "conejo.seed_tesis.v1"
TABLES = ("categories", "products", "product_state_histories", "suppliers", "customers", "users", "sales", "sale_lines",
          "stock_movements", "purchase_orders", "purchase_order_lines", "purchase_receptions",
          "purchase_reception_lines", "quotes", "quote_lines", "orders", "order_lines",
          "delivery_notes", "delivery_note_items")
DELETE_ORDER = ("quote_lines", "order_lines", "quotes", "orders", "sale_lines", "purchase_reception_lines",
                "purchase_receptions", "purchase_order_lines", "sales", "purchase_orders",
                "delivery_note_items", "delivery_notes",
                "stock_movements", "product_state_histories", "products", "categories", "customers", "suppliers", "users")


class SeedError(ValueError):
    """Only safe, deliberately written CLI messages."""


def require(condition, message):
    if not condition:
        raise SeedError(message)


def configured_url():
    # Never import app.config or load .env. Validate before imports/connections/writes.
    require(os.getenv("DB_HOST") == "localhost" and os.getenv("DB_NAME") == DATABASE,
            "Se requieren DB_HOST=localhost y DB_NAME=ferreteria_conejo_tesis explícitos.")
    require(not any(os.getenv(k) for k in ("DATABASE_URL", "SQLALCHEMY_DATABASE_URI")),
            "Retire las URL alternativas de conexión antes de continuar.")
    require(os.getenv("DB_USER") == "root", "Se requiere DB_USER=root explícito.")
    password = "" if os.getenv("DB_ALLOW_EMPTY_PASSWORD") == "1" else os.getenv("DB_PASSWORD")
    require(bool(password) or os.getenv("DB_ALLOW_EMPTY_PASSWORD") == "1",
            "Contraseña vacía requiere DB_ALLOW_EMPTY_PASSWORD=1; no se lee .env.")
    port = os.getenv("DB_PORT")
    require(port == "3306", "Se requiere DB_PORT=3306 explícito.")
    addresses = {r[4][0] for r in socket.getaddrinfo("localhost", int(port), type=socket.SOCK_STREAM)}
    require(addresses and all(ipaddress.ip_address(a).is_loopback for a in addresses),
            "localhost debe resolver exclusivamente a loopback.")
    from sqlalchemy.engine import URL

    return URL.create("mysql+pymysql", username=os.environ["DB_USER"], password=password,
        host="127.0.0.1" if "127.0.0.1" in addresses else sorted(addresses)[0], port=int(port),
        database=DATABASE, query={"charset": "utf8mb4"})


def manifest_table():
    from sqlalchemy import Table, MetaData, Column, String, Integer, Boolean, JSON, DateTime

    return Table("seed_manifests", MetaData(),
        Column("dataset", String(64), primary_key=True), Column("version", Integer, nullable=False),
        Column("synthetic", Boolean, nullable=False), Column("seed", Integer, nullable=False),
        Column("run_uuid", String(36), nullable=False), Column("environment_uuid", String(36), nullable=False),
        Column("database_name", String(64), nullable=False), Column("created_at", DateTime, nullable=False),
        Column("payload", JSON, nullable=False), Column("payload_sha256", String(64), nullable=False),
        mysql_engine="InnoDB", mysql_charset="utf8mb4")


def digest(value):
    encoded = json.dumps(value, default=str, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load_domain():
    # app imports Config, but no environment files may be read by this CLI.
    with patch("dotenv.load_dotenv", return_value=False):
        from app.extensions import db, login_manager
        from app import models
        from app.services import (category_service, customer_service, product_service, supplier_service,
            inventory_service, sale_service, purchase_service, quote_service, order_service,
            delivery_note_service, notification_service, sales_common)
    return SimpleNamespace(**locals())


@contextmanager
def domain_session(domain, connection, created=None):
    from sqlalchemy import event
    from sqlalchemy.orm import scoped_session, sessionmaker

    original = domain.db.session
    session = scoped_session(sessionmaker(bind=connection, join_transaction_mode="create_savepoint"))
    domain.db.session = session

    def track(current, _context):
        for row in current.new:
            name = row.__table__.name
            require(name in TABLES, "El servicio creó una entidad no prevista; se revierte todo.")
            if created is not None:
                created[name].add(row.id)

    event.listen(session(), "after_flush", track)
    try:
        with patch.object(domain.notification_service, "_safe", lambda *args, **kwargs: None), \
             patch.object(domain.notification_service, "order_event", lambda *args, **kwargs: None), \
             patch.object(domain.notification_service, "sale_created", lambda *args, **kwargs: None):
            yield session
    finally:
        session.remove()
        domain.db.session = original


def mysql_preflight(connection, prepared=True):
    from sqlalchemy import inspect, text

    require(connection.dialect.name == "mysql", "La CLI requiere MySQL local.")
    require(connection.execute(text("SELECT DATABASE()")).scalar() == DATABASE, "Base efectiva no autorizada.")
    inspector = inspect(connection)
    needed = set(TABLES) | ({"seed_manifests"} if prepared else set())
    require(needed <= set(inspector.get_table_names()), "Faltan tablas; prepare el esquema por separado.")
    engines = dict(connection.execute(text("SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()")).all())
    require(all(engines.get(t) == "InnoDB" for t in needed), "Se requiere InnoDB en todas las tablas afectadas.")
    cross = connection.execute(text("SELECT 1 FROM information_schema.KEY_COLUMN_USAGE WHERE (REFERENCED_TABLE_SCHEMA=DATABASE() AND TABLE_SCHEMA<>DATABASE()) OR (TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_SCHEMA<>DATABASE()) LIMIT 1")).first()
    require(cross is None, "Hay relaciones entre bases; operación cancelada.")
    triggers = connection.execute(text("SELECT EVENT_OBJECT_TABLE FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()")).scalars()
    require(not set(triggers).intersection(needed | {"seed_manifests"}), "Hay triggers no revisados; operación cancelada.")
    if prepared:
        validate_registry_schema(connection)


def validate_registry_schema(connection):
    from sqlalchemy import inspect

    inspector, expected = inspect(connection), manifest_table()
    actual = {c["name"]: c for c in inspector.get_columns(expected.name)}
    require(set(actual) == set(expected.c.keys()), "Esquema de manifiesto desconocido.")
    require(inspector.get_pk_constraint(expected.name)["constrained_columns"] == ["dataset"],
            "Clave de manifiesto desconocida.")
    require(not inspector.get_foreign_keys(expected.name), "El manifiesto no debe tener claves foráneas.")
    for name in inspector.get_table_names():
        require(not any(fk["referred_table"] == expected.name for fk in inspector.get_foreign_keys(name)),
                "Hay referencias a la tabla interna; operación cancelada.")
    for column in expected.c:
        found = actual[column.name]
        compatible = found["type"]._type_affinity == column.type._type_affinity
        if column.name == "synthetic" and connection.dialect.name == "mysql":
            from sqlalchemy.dialects.mysql import TINYINT

            compatible = isinstance(found["type"], TINYINT) and found["type"].display_width == 1
        if column.name == "payload" and getattr(connection.dialect, 'is_mariadb', False):
            compatible = mariadb_json_payload(connection, found['type'])
        require(compatible
                and found["nullable"] == column.nullable
                and getattr(found["type"], "length", None) == getattr(column.type, "length", None),
                "Tipo de columna de manifiesto incompatible.")


def mariadb_json_payload(connection, column_type):
    from sqlalchemy import text
    from sqlalchemy.dialects.mysql import LONGTEXT

    if not isinstance(column_type, LONGTEXT):
        return False
    checks = connection.execute(text(
        "SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='seed_manifests' "
        "AND CONSTRAINT_NAME='payload'" )).scalars()
    return any(''.join(check.lower().split()).replace('`', '') == 'json_valid(payload)' for check in checks)


def validate_domain_schema(connection, domain):
    from sqlalchemy import inspect

    inspector = inspect(connection)
    for name in TABLES:
        expected = domain.db.metadata.tables[name]
        require({c["name"] for c in inspector.get_columns(name)} == set(expected.c.keys()),
                "El esquema comercial difiere de los modelos; no se ignorarán columnas desconocidas.")
        require(inspector.get_pk_constraint(name)["constrained_columns"] == ["id"],
                "Clave comercial no reconocida.")


@contextmanager
def locked(connection):
    from sqlalchemy import text

    require(connection.execute(text("SELECT GET_LOCK(:name, 0)"), {"name": LOCK}).scalar() == 1,
            "Otra preparación o seed está en curso.")
    connection.commit()
    try:
        yield
    finally:
        connection.rollback()
        connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": LOCK})
        connection.commit()


def select_rows(domain, name, ids):
    from sqlalchemy import select

    target = domain.db.metadata.tables[name]
    return domain.db.session.execute(select(target).where(target.c.id.in_(ids)).order_by(target.c.id)
                                     .with_for_update()).mappings().all()


def fingerprints(domain, rows):
    result = {}
    for name in TABLES:
        values = select_rows(domain, name, rows[name])
        require(len(values) == len(rows[name]), "Faltan registros del seed; no se borrará nada.")
        result[name] = digest([dict(value) for value in values])
    return result


def validate_manifest(record):
    require(record["dataset"] == DATASET and record["version"] == VERSION and record["synthetic"] is True
            and record["seed"] == SEED and record["database_name"] == DATABASE, "Manifiesto no reconocido.")
    for key in ("run_uuid", "environment_uuid"):
        require(str(UUID(record[key])) == record[key], "UUID de manifiesto inválido.")
    payload = record["payload"]
    require(isinstance(payload, dict) and set(payload) == {"rows", "hashes", "start", "end"}, "Contenido de manifiesto inválido.")
    require(payload["start"] == str(START) and payload["end"] == str(END)
            and digest(payload) == record["payload_sha256"], "Manifiesto alterado.")
    require(set(payload["rows"]) == set(TABLES) and set(payload["hashes"]) == set(TABLES), "Tablas de manifiesto desconocidas.")
    for ids in payload["rows"].values():
        require(isinstance(ids, list) and ids and all(type(i) is int and i > 0 for i in ids)
                and ids == sorted(set(ids)), "Identificadores de manifiesto inválidos.")
    return payload


def existing_manifest(domain):
    from sqlalchemy import select

    records = domain.db.session.execute(select(manifest_table()).with_for_update()).mappings().all()
    if not records:
        for name in TABLES:
            target = domain.db.metadata.tables[name]
            require(domain.db.session.execute(select(target.c.id).limit(1).with_for_update()).first() is None,
                    "Hay datos sin manifiesto; no se adoptarán ni duplicarán registros.")
        return None
    require(len(records) == 1, "Hay conjuntos desconocidos en el registro interno.")
    payload = validate_manifest(records[0])
    require(fingerprints(domain, payload["rows"]) == payload["hashes"],
            "Hay modificaciones posteriores; reset cancelado sin borrar cambios.")
    return dict(records[0])


def reject_external_references(connection, domain, rows):
    from sqlalchemy import inspect, and_, or_

    inspector = inspect(connection)
    for name in inspector.get_table_names():
        for fk in inspector.get_foreign_keys(name):
            parent = fk["referred_table"]
            if parent in rows:
                check_foreign_key(connection, domain, rows, name, fk)
    notification = domain.models.Notification
    kinds = (("product", "products"), ("sale", "sales"), ("purchase_order", "purchase_orders"),
             ("order", "orders"), ("quote", "quotes"))
    terms = [and_(notification.entity_type == kind, notification.entity_id.in_(rows[name])) for kind, name in kinds]
    require(domain.db.session.query(notification.id).filter(or_(*terms)).with_for_update().first() is None,
            "Hay notificaciones posteriores; reset cancelado.")
    require(domain.db.session.query(domain.models.InventorySnapshot.id).filter(
        domain.models.InventorySnapshot.category_id.in_(rows["categories"])).with_for_update().first() is None,
        "Hay cierres posteriores; reset cancelado.")


def check_foreign_key(connection, domain, rows, name, fk):
    from sqlalchemy import Table, MetaData, select

    require(fk.get("referred_schema") in (None, DATABASE) and fk["referred_columns"] == ["id"]
            and len(fk["constrained_columns"]) == 1, "Relación desconocida; reset cancelado.")
    child = Table(name, MetaData(), autoload_with=connection)
    query = select(child.c[fk["constrained_columns"][0]]).where(
        child.c[fk["constrained_columns"][0]].in_(rows[fk["referred_table"]]))
    if name in rows:
        query = query.where(child.c.id.not_in(rows[name]))
    require(domain.db.session.execute(query.limit(1).with_for_update()).first() is None,
            "Hay referencias externas al seed; reset cancelado.")


def write_manifest(domain, rows, previous=None):
    from sqlalchemy import insert

    payload = dict(rows=rows, hashes=fingerprints(domain, rows), start=str(START), end=str(END))
    record = dict(dataset=DATASET, version=VERSION, synthetic=True, seed=SEED, run_uuid=str(uuid4()),
        environment_uuid=previous["environment_uuid"] if previous else str(uuid4()), database_name=DATABASE,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None), payload=payload, payload_sha256=digest(payload))
    domain.db.session.execute(insert(manifest_table()).values(**record))
    domain.db.session.commit()  # SAVEPOINT only; the outer transaction owns durability.
    return existing_manifest(domain)


def reset_rows(domain, rows):
    from sqlalchemy import delete

    for name in DELETE_ORDER:
        target = domain.db.metadata.tables[name]
        domain.db.session.execute(delete(target).where(target.c.id.in_(rows[name])))
    registry = manifest_table()
    domain.db.session.execute(delete(registry).where(registry.c.dataset == DATASET))
    domain.db.session.commit()
    domain.db.session.expire_all()
