"""Migración manual, idempotente y no destructiva del histórico CSV v1.

La instalación nueva crea las tres tablas con el contrato SQL aprobado. En
MySQL/MariaDB, una instalación legacy se amplía con columnas canónicas, se
retroalimentan sus valores y se conservan las columnas anteriores. Nunca se
eliminan tablas, columnas ni filas preexistentes.

SQLite solo admite instalación nueva o un esquema ya canónico: reconstruir
una tabla legacy para cambiar NOT NULL/FK implicaría borrarla, operación que
esta migración rechaza deliberadamente.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import (  # noqa: E402
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Integer,
    JSON,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.schema import AddConstraint  # noqa: E402
from sqlalchemy.sql.sqltypes import BigInteger  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    HistoricalDemandRecord,
    HistoricalImport,
    HistoricalImportError,
)
from app.models.historical_demand_record import (  # noqa: E402
    IMMUTABLE_CONFIRMED_COLUMN_NAMES,
)

HISTORICAL_MODELS = (
    HistoricalImport,
    HistoricalDemandRecord,
    HistoricalImportError,
)
HISTORICAL_TABLES = tuple(model.__tablename__ for model in HISTORICAL_MODELS)
IMMUTABILITY_TRIGGER_NAMES = {
    "update": "trg_hist_records_immutable_update",
    "delete": "trg_hist_records_immutable_delete",
}
IMMUTABILITY_TRIGGER_MESSAGE = "Confirmed historical records are immutable"


@dataclass(frozen=True)
class UpgradeRule:
    legacy_column: str | None = None
    fallback_sql: str | None = None
    expression: str | None = None


# Expresiones constantes y nombres de columna están definidos en código; nunca
# provienen de archivos CSV ni de parámetros del usuario.
UPGRADE_RULES: dict[str, dict[str, UpgradeRule]] = {
    "historical_imports": {
        "file_size": UpgradeRule("file_size_bytes"),
        "file_sha256": UpgradeRule("sha256"),
        "file_format": UpgradeRule(fallback_sql="'csv'"),
        "period_start": UpgradeRule(fallback_sql="'2025-01-01'"),
        "period_end": UpgradeRule(fallback_sql="'2025-12-31'"),
        "parser_version": UpgradeRule(
            "schema_version", "'historical-parser-v1'"
        ),
        "mapping_json": UpgradeRule("column_mapping_json"),
        "error_rows": UpgradeRule("error_count", "0"),
        "warning_rows": UpgradeRule("warning_count", "0"),
        "pending_match_rows": UpgradeRule("review_count", "0"),
        "revert_reason": UpgradeRule("reversal_reason"),
    },
    "historical_demand_records": {
        "import_id": UpgradeRule("historical_import_id"),
        "source_record_id": UpgradeRule("source_record_id_original"),
        "source_line_id": UpgradeRule("source_line_id_original"),
        "document_number": UpgradeRule("document_number_original"),
        "original_product_code": UpgradeRule("product_code_original"),
        "normalized_product_code": UpgradeRule("product_code_normalized"),
        "original_product_name": UpgradeRule("product_name_original"),
        "normalized_product_name": UpgradeRule("product_name_normalized"),
    },
    "historical_import_errors": {
        "import_id": UpgradeRule("historical_import_id"),
        "safe_message": UpgradeRule("message"),
        "redacted_value": UpgradeRule(),
        "resolved": UpgradeRule(
            expression=(
                "CASE WHEN {resolution_status} = 'resolved' THEN 1 ELSE 0 END"
            )
        ),
        "resolution_note": UpgradeRule(),
    },
}

_SPACE_RE = re.compile(r"\s+")


def _quote(identifier: str) -> str:
    return db.engine.dialect.identifier_preparer.quote(identifier)


def _dialect_name() -> str:
    return db.engine.dialect.name.casefold()


def _is_mysql() -> bool:
    return _dialect_name() in {"mysql", "mariadb"}


def _is_sqlite() -> bool:
    return _dialect_name() == "sqlite"


def _trigger_catalog(connection) -> dict[str, dict[str, str]]:
    """Obtiene triggers de la tabla histórica sin depender de nombres CSV."""
    table_name = HistoricalDemandRecord.__tablename__
    if _is_mysql():
        rows = connection.execute(
            text(
                """
                SELECT
                    TRIGGER_NAME AS trigger_name,
                    ACTION_TIMING AS action_timing,
                    EVENT_MANIPULATION AS event_manipulation,
                    ACTION_STATEMENT AS action_statement
                FROM information_schema.TRIGGERS
                WHERE TRIGGER_SCHEMA = DATABASE()
                  AND EVENT_OBJECT_TABLE = :table_name
                """
            ),
            {"table_name": table_name},
        ).mappings()
        return {
            str(row["trigger_name"]): {
                "timing": str(row["action_timing"]),
                "event": str(row["event_manipulation"]),
                "sql": str(row["action_statement"]),
            }
            for row in rows
        }
    if _is_sqlite():
        rows = connection.execute(
            text(
                """
                SELECT name, sql
                FROM sqlite_master
                WHERE type = 'trigger' AND tbl_name = :table_name
                """
            ),
            {"table_name": table_name},
        ).mappings()
        return {
            str(row["name"]): {"sql": str(row["sql"] or "")}
            for row in rows
        }
    raise RuntimeError(
        "La protección SQL de inmutabilidad solo está implementada para "
        "MySQL/MariaDB y SQLite."
    )


def _mysql_immutability_trigger(event_name: str) -> str:
    records = _quote(HistoricalDemandRecord.__tablename__)
    imports = _quote(HistoricalImport.__tablename__)
    import_id = _quote("import_id")
    trigger_name = _quote(IMMUTABILITY_TRIGGER_NAMES[event_name])
    protected_batch = (
        f"(SELECT COUNT(*) FROM {imports} "
        f"WHERE {_quote('id')} = OLD.{import_id} "
        f"AND {_quote('status')} IN ('confirmed', 'reverted')) > 0"
    )
    if event_name == "update":
        comparisons = "\n            OR ".join(
            f"NOT (OLD.{_quote(column)} <=> NEW.{_quote(column)})"
            for column in IMMUTABLE_CONFIRMED_COLUMN_NAMES
        )
        condition = f"{protected_batch}\n        AND (\n            {comparisons}\n        )"
        event_sql = "UPDATE"
    else:
        condition = protected_batch
        event_sql = "DELETE"
    return (
        f"CREATE TRIGGER {trigger_name}\n"
        f"BEFORE {event_sql} ON {records}\n"
        "FOR EACH ROW\n"
        "BEGIN\n"
        f"    IF {condition} THEN\n"
        "        SIGNAL SQLSTATE '45000'\n"
        f"            SET MESSAGE_TEXT = '{IMMUTABILITY_TRIGGER_MESSAGE}';\n"
        "    END IF;\n"
        "END"
    )


def _sqlite_immutability_trigger(event_name: str) -> str:
    records = _quote(HistoricalDemandRecord.__tablename__)
    imports = _quote(HistoricalImport.__tablename__)
    import_id = _quote("import_id")
    trigger_name = _quote(IMMUTABILITY_TRIGGER_NAMES[event_name])
    protected_batch = (
        f"EXISTS (SELECT 1 FROM {imports} "
        f"WHERE {_quote('id')} = OLD.{import_id} "
        f"AND {_quote('status')} IN ('confirmed', 'reverted'))"
    )
    if event_name == "update":
        comparisons = "\n        OR ".join(
            f"OLD.{_quote(column)} IS NOT NEW.{_quote(column)}"
            for column in IMMUTABLE_CONFIRMED_COLUMN_NAMES
        )
        condition = f"{protected_batch}\n    AND (\n        {comparisons}\n    )"
        event_sql = "UPDATE"
    else:
        condition = protected_batch
        event_sql = "DELETE"
    return (
        f"CREATE TRIGGER {trigger_name}\n"
        f"BEFORE {event_sql} ON {records}\n"
        "FOR EACH ROW\n"
        f"WHEN {condition}\n"
        "BEGIN\n"
        f"    SELECT RAISE(ABORT, '{IMMUTABILITY_TRIGGER_MESSAGE}');\n"
        "END"
    )


def _trigger_is_compatible(
    trigger_name: str, definition: dict[str, str]
) -> bool:
    normalized = _normalize_sql(definition.get("sql"))
    expected_event = next(
        event_name
        for event_name, name in IMMUTABILITY_TRIGGER_NAMES.items()
        if name == trigger_name
    )
    common_markers = (
        HistoricalImport.__tablename__,
        "old.import_id",
        "'confirmed'",
        "'reverted'",
        IMMUTABILITY_TRIGGER_MESSAGE.casefold(),
    )
    if not all(marker in normalized for marker in common_markers):
        return False

    if _is_mysql():
        if definition.get("timing", "").casefold() != "before":
            return False
        if definition.get("event", "").casefold() != expected_event:
            return False
        if "signal sqlstate" not in normalized:
            return False
    else:
        header = (
            f"before {expected_event} on "
            f"{HistoricalDemandRecord.__tablename__}"
        )
        if header not in normalized or "raise(abort" not in normalized.replace(" ", ""):
            return False

    if expected_event == "update":
        return all(
            f"old.{column}" in normalized and f"new.{column}" in normalized
            for column in IMMUTABLE_CONFIRMED_COLUMN_NAMES
        )
    return True


def _ensure_immutability_triggers(connection) -> None:
    catalog = _trigger_catalog(connection)
    builder = (
        _mysql_immutability_trigger if _is_mysql() else _sqlite_immutability_trigger
    )
    for event_name, trigger_name in IMMUTABILITY_TRIGGER_NAMES.items():
        existing = catalog.get(trigger_name)
        if existing is not None:
            if not _trigger_is_compatible(trigger_name, existing):
                raise RuntimeError(
                    f"{trigger_name}: existe, pero no coincide con la "
                    "protección de inmutabilidad esperada."
                )
            print(f"[OK] Trigger de inmutabilidad existente: {trigger_name}.")
            continue
        connection.exec_driver_sql(builder(event_name))
        refreshed = _trigger_catalog(connection).get(trigger_name)
        if refreshed is None or not _trigger_is_compatible(
            trigger_name, refreshed
        ):
            raise RuntimeError(
                f"{trigger_name}: no pudo crearse o verificarse correctamente."
            )
        catalog[trigger_name] = refreshed
        print(f"[OK] Trigger de inmutabilidad creado: {trigger_name}.")


def _verify_immutability_triggers() -> bool:
    with db.engine.connect() as connection:
        catalog = _trigger_catalog(connection)
    invalid = sorted(
        trigger_name
        for trigger_name in IMMUTABILITY_TRIGGER_NAMES.values()
        if trigger_name not in catalog
        or not _trigger_is_compatible(trigger_name, catalog[trigger_name])
    )
    ok = not invalid
    print(
        f"[{'OK' if ok else 'ERROR'}] Triggers de inmutabilidad: "
        f"faltantes/incompatibles={invalid}."
    )
    return ok


def _operational_snapshot() -> dict[str, dict[str, object]]:
    """Snapshot no mutante: conteo y rango de la primera PK por tabla."""
    inspector = inspect(db.engine)
    result: dict[str, dict[str, object]] = {}
    with db.engine.connect() as connection:
        for table_name in sorted(
            set(inspector.get_table_names()) - set(HISTORICAL_TABLES)
        ):
            metadata = MetaData()
            table = Table(table_name, metadata, autoload_with=connection)
            pk_columns = list(table.primary_key.columns)
            count = int(
                connection.execute(
                    select(func.count()).select_from(table)
                ).scalar_one()
            )
            snapshot: dict[str, object] = {"count": count}
            if len(pk_columns) == 1:
                pk = pk_columns[0]
                pk_min, pk_max = connection.execute(
                    select(func.min(pk), func.max(pk))
                ).one()
                snapshot.update(
                    {"pk_column": pk.name, "pk_min": pk_min, "pk_max": pk_max}
                )
            result[table_name] = snapshot
    return result


def _table_count(connection, table_name: str) -> int:
    statement = text(f"SELECT COUNT(*) FROM {_quote(table_name)}")
    return int(connection.execute(statement).scalar_one())


def _mysql_engine(table_name: str) -> str | None:
    if not _is_mysql():
        return None
    options = inspect(db.engine).get_table_options(table_name)
    value = options.get("mysql_engine") or options.get("engine")
    return str(value).casefold() if value else None


def _resolved_expected_type(column):
    return column.type.dialect_impl(db.engine.dialect)


def _type_compatible(actual_type, expected_column) -> bool:
    """Comparación semántica estable entre reflexión y tipo declarado."""
    expected = _resolved_expected_type(expected_column)
    if isinstance(expected, BigInteger):
        return isinstance(actual_type, BigInteger)
    if isinstance(expected, Boolean):
        actual_text = str(actual_type).casefold().replace(" ", "")
        tinyint_boolean = actual_text.startswith("tinyint") and getattr(
            actual_type, "display_width", 1
        ) in (None, 1)
        return (
            isinstance(actual_type, Boolean)
            or actual_text in {"boolean", "bool"}
            or tinyint_boolean
        )
    if isinstance(expected, Integer):
        return isinstance(actual_type, Integer) and not isinstance(
            actual_type, BigInteger
        )
    if isinstance(expected, String):
        return isinstance(actual_type, String) and (
            expected.length is None
            or getattr(actual_type, "length", None) == expected.length
        )
    if isinstance(expected, Numeric):
        return (
            isinstance(actual_type, Numeric)
            and getattr(actual_type, "precision", None) == expected.precision
            and getattr(actual_type, "scale", None) == expected.scale
        )
    if isinstance(expected, DateTime):
        return isinstance(actual_type, DateTime)
    if isinstance(expected, Date):
        return isinstance(actual_type, Date) and not isinstance(actual_type, DateTime)
    if isinstance(expected, JSON):
        if isinstance(actual_type, JSON):
            return True
        # MariaDB refleja en algunas versiones su alias JSON como LONGTEXT.
        return bool(
            getattr(db.engine.dialect, "is_mariadb", False)
            and "text" in str(actual_type).casefold()
        )
    return expected._type_affinity is actual_type._type_affinity


def _compile_type(column) -> str:
    return column.type.compile(dialect=db.engine.dialect)


def _reflected_type_signature(column_type) -> tuple[object, ...]:
    """Firma suficiente para detectar FKs incompatibles, incluido UNSIGNED."""
    unsigned = bool(getattr(column_type, "unsigned", False))
    if isinstance(column_type, BigInteger):
        return ("bigint", unsigned)
    if isinstance(column_type, Integer):
        return ("integer", unsigned)
    if isinstance(column_type, String):
        return ("string", getattr(column_type, "length", None))
    if isinstance(column_type, Numeric):
        return (
            "numeric",
            getattr(column_type, "precision", None),
            getattr(column_type, "scale", None),
            unsigned,
        )
    return (column_type._type_affinity,)


def _normalize_sql(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.strip().casefold().replace("`", "")
    # MySQL puede reflejar literales como _utf8mb4'valor'. El introductor no
    # cambia la semántica del CHECK declarado por el modelo.
    # El introductor aparece fuera del literal (por ejemplo,
    # ``_utf8mb4'uploaded'``). Excluir comillas y caracteres de palabra en el
    # lookbehind evita confundir sufijos válidos como ``'dry_run_ready'`` con
    # un nombre de charset.
    normalized = re.sub(
        r"(?<!['a-z0-9_])_[a-z0-9]+(?=')",
        "",
        normalized,
    )
    return _SPACE_RE.sub(" ", normalized)


def _check_signature(value: str | None) -> str:
    """Firma textual tolerante a espacios/paréntesis agregados por el dialecto."""
    normalized = _normalize_sql(value)
    return re.sub(r"[\s()]", "", normalized)


def _actual_indexes(table_name: str) -> set[tuple[tuple[str, ...], bool]]:
    inspector = inspect(db.engine)
    result: set[tuple[tuple[str, ...], bool]] = set()
    for index in inspector.get_indexes(table_name):
        result.add(
            (
                tuple(index.get("column_names") or ()),
                bool(index.get("unique")),
            )
        )
    for constraint in inspector.get_unique_constraints(table_name):
        result.add((tuple(constraint.get("column_names") or ()), True))
    return result


def _expected_indexes(table) -> set[tuple[tuple[str, ...], bool]]:
    result = {
        (tuple(column.name for column in index.columns), bool(index.unique))
        for index in table.indexes
    }
    result.update(
        (tuple(column.name for column in constraint.columns), True)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    )
    return result


def _ondelete(value: str | None) -> str:
    # MySQL suele reflejar RESTRICT como None porque es su comportamiento por defecto.
    return (value or "RESTRICT").upper()


def _actual_foreign_keys(
    table_name: str,
) -> set[tuple[tuple[str, ...], str, tuple[str, ...], str]]:
    result = set()
    for foreign_key in inspect(db.engine).get_foreign_keys(table_name):
        options = foreign_key.get("options") or {}
        result.add(
            (
                tuple(foreign_key.get("constrained_columns") or ()),
                foreign_key.get("referred_table"),
                tuple(foreign_key.get("referred_columns") or ()),
                _ondelete(options.get("ondelete")),
            )
        )
    return result


def _expected_foreign_keys(
    table,
) -> set[tuple[tuple[str, ...], str, tuple[str, ...], str]]:
    result = set()
    for constraint in table.foreign_key_constraints:
        local = tuple(element.parent.name for element in constraint.elements)
        target_tables = {element.column.table.name for element in constraint.elements}
        if len(target_tables) != 1:
            raise RuntimeError("Se detectó una FK compuesta hacia tablas distintas.")
        target_table = next(iter(target_tables))
        remote = tuple(element.column.name for element in constraint.elements)
        result.add((local, target_table, remote, _ondelete(constraint.ondelete)))
    return result


def _expected_checks(table) -> dict[str, str]:
    return {
        constraint.name: _normalize_sql(str(constraint.sqltext))
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }


def _actual_checks(table_name: str) -> dict[str, str]:
    return {
        item.get("name"): _normalize_sql(item.get("sqltext"))
        for item in inspect(db.engine).get_check_constraints(table_name)
        if item.get("name")
    }


def _preflight_existing_table(model) -> None:
    table = model.__table__
    table_name = table.name
    inspector = inspect(db.engine)
    actual_columns = {
        item["name"]: item for item in inspector.get_columns(table_name)
    }
    expected_columns = {column.name: column for column in table.columns}
    missing = set(expected_columns) - set(actual_columns)

    if _dialect_name() == "sqlite" and missing:
        raise RuntimeError(
            f"{table_name}: SQLite legacy requiere reconstruir la tabla para "
            "garantizar NOT NULL/FK; se aborta sin aplicar DDL destructivo."
        )
    if _dialect_name() == "sqlite":
        legacy_not_null = sorted(
            rule.legacy_column
            for rule in UPGRADE_RULES.get(table_name, {}).values()
            if rule.legacy_column
            and rule.legacy_column in actual_columns
            and not actual_columns[rule.legacy_column].get("nullable", True)
        )
        if legacy_not_null:
            raise RuntimeError(
                f"{table_name}: columnas legacy NOT NULL impedirían inserts "
                f"canónicos en SQLite: {legacy_not_null}."
            )
    if missing and not _is_mysql():
        raise RuntimeError(
            f"{table_name}: upgrade legacy no soportado para {_dialect_name()}."
        )

    known_rules = UPGRADE_RULES.get(table_name, {})
    unknown_missing = missing - set(known_rules)
    if unknown_missing:
        raise RuntimeError(
            f"{table_name}: faltan columnas sin regla segura de backfill: "
            f"{sorted(unknown_missing)}"
        )

    if _is_mysql() and _mysql_engine(table_name) != "innodb":
        raise RuntimeError(
            f"{table_name}: el motor debe ser InnoDB antes de migrar."
        )

    actual_pk = tuple(inspector.get_pk_constraint(table_name).get("constrained_columns") or ())
    expected_pk = tuple(column.name for column in table.primary_key.columns)
    if actual_pk != expected_pk:
        raise RuntimeError(
            f"{table_name}: PK incompatible {actual_pk}; se esperaba {expected_pk}."
        )

    for name in set(actual_columns) & set(expected_columns):
        if not _type_compatible(actual_columns[name]["type"], expected_columns[name]):
            raise RuntimeError(
                f"{table_name}.{name}: tipo incompatible "
                f"{actual_columns[name]['type']}; esperado {_compile_type(expected_columns[name])}."
            )

    with db.engine.connect() as connection:
        existing_rows = _table_count(connection, table_name)
    if existing_rows:
        for name in missing:
            rule = known_rules[name]
            if (
                not expected_columns[name].nullable
                and rule.legacy_column not in actual_columns
                and rule.fallback_sql is None
                and rule.expression is None
            ):
                raise RuntimeError(
                    f"{table_name}.{name}: no existe fuente segura para el backfill."
                )


def _backfill_expression(
    rule: UpgradeRule, actual_columns: set[str]
) -> str | None:
    if rule.expression:
        return rule.expression.format(
            resolution_status=_quote("resolution_status")
        )
    if rule.legacy_column and rule.legacy_column in actual_columns:
        legacy = _quote(rule.legacy_column)
        if rule.fallback_sql is not None:
            return f"COALESCE({legacy}, {rule.fallback_sql})"
        return legacy
    return rule.fallback_sql


def _set_mysql_nullable(connection, table_name: str, column_info: dict) -> None:
    if column_info.get("nullable", True):
        return
    actual_type = column_info["type"].compile(dialect=db.engine.dialect)
    statement = text(
        f"ALTER TABLE {_quote(table_name)} MODIFY COLUMN "
        f"{_quote(column_info['name'])} {actual_type} NULL"
    )
    connection.execute(statement)
    print(
        f"[OK] Compatibilidad legacy: {table_name}.{column_info['name']} "
        "se conserva nullable."
    )


def _upgrade_existing_table(connection, model) -> None:
    table = model.__table__
    table_name = table.name
    rules = UPGRADE_RULES.get(table_name, {})
    inspector = inspect(db.engine)
    actual_info = {item["name"]: item for item in inspector.get_columns(table_name)}
    actual_names = set(actual_info)

    for column in table.columns:
        if column.name in actual_names:
            continue
        statement = text(
            f"ALTER TABLE {_quote(table_name)} ADD COLUMN "
            f"{_quote(column.name)} {_compile_type(column)} NULL"
        )
        connection.execute(statement)
        print(f"[OK] Columna canónica añadida: {table_name}.{column.name}.")
        actual_names.add(column.name)

    for column in table.columns:
        rule = rules.get(column.name)
        if rule is None:
            continue
        expression = _backfill_expression(rule, actual_names)
        if expression is None:
            continue
        statement = text(
            f"UPDATE {_quote(table_name)} SET {_quote(column.name)} = {expression} "
            f"WHERE {_quote(column.name)} IS NULL"
        )
        connection.execute(statement)
        print(f"[OK] Backfill verificado: {table_name}.{column.name}.")

    # La reflexión se renueva porque una ejecución previa pudo quedar a mitad de
    # DDL (autocommit de MySQL); el proceso es reiniciable.
    actual_info = {
        item["name"]: item for item in inspect(db.engine).get_columns(table_name)
    }
    for column in table.columns:
        if column.nullable or not actual_info[column.name].get("nullable", True):
            continue
        null_count = int(
            connection.execute(
                text(
                    f"SELECT COUNT(*) FROM {_quote(table_name)} "
                    f"WHERE {_quote(column.name)} IS NULL"
                )
            ).scalar_one()
        )
        if null_count:
            raise RuntimeError(
                f"{table_name}.{column.name}: quedan {null_count} NULL; "
                "no se fuerza NOT NULL."
            )
        connection.execute(
            text(
                f"ALTER TABLE {_quote(table_name)} MODIFY COLUMN "
                f"{_quote(column.name)} {_compile_type(column)} NOT NULL"
            )
        )
        print(f"[OK] NOT NULL aplicado: {table_name}.{column.name}.")

    # Las columnas legacy se conservan, pero dejan de bloquear inserts del
    # modelo canónico, que ya no escribe en ellas.
    actual_info = {
        item["name"]: item for item in inspect(db.engine).get_columns(table_name)
    }
    legacy_names = {
        rule.legacy_column
        for rule in rules.values()
        if rule.legacy_column and rule.legacy_column in actual_info
    }
    for legacy_name in sorted(legacy_names):
        _set_mysql_nullable(connection, table_name, actual_info[legacy_name])


def _ensure_indexes(connection, model) -> None:
    table = model.__table__
    actual = _actual_indexes(table.name)
    for index in sorted(table.indexes, key=lambda item: item.name or ""):
        signature = (
            tuple(column.name for column in index.columns),
            bool(index.unique),
        )
        if signature in actual:
            continue
        index.create(bind=connection, checkfirst=True)
        actual.add(signature)
        print(f"[OK] Índice añadido: {table.name}.{index.name}.")


def _ensure_constraints(connection, model) -> None:
    table = model.__table__
    if not _is_mysql():
        return

    actual_indexes = _actual_indexes(table.name)
    for constraint in sorted(
        (
            item
            for item in table.constraints
            if isinstance(item, UniqueConstraint)
        ),
        key=lambda item: item.name or "",
    ):
        signature = (tuple(column.name for column in constraint.columns), True)
        if signature in actual_indexes:
            continue
        connection.execute(AddConstraint(constraint))
        actual_indexes.add(signature)
        print(f"[OK] UNIQUE añadido: {table.name}.{constraint.name}.")

    actual_checks = _actual_checks(table.name)
    for constraint in sorted(
        (
            item
            for item in table.constraints
            if isinstance(item, CheckConstraint) and item.name
        ),
        key=lambda item: item.name,
    ):
        if constraint.name in actual_checks:
            continue
        connection.execute(AddConstraint(constraint))
        print(f"[OK] CHECK añadido: {table.name}.{constraint.name}.")

    actual_fks = _actual_foreign_keys(table.name)
    for constraint in sorted(
        table.foreign_key_constraints, key=lambda item: item.name or ""
    ):
        expected = next(
            item
            for item in _expected_foreign_keys(table)
            if item[0] == tuple(element.parent.name for element in constraint.elements)
        )
        if expected in actual_fks:
            continue
        base = expected[:3]
        conflicting = [item for item in actual_fks if item[:3] == base]
        if conflicting:
            raise RuntimeError(
                f"{table.name}{base[0]}: FK existente con ON DELETE incompatible."
            )
        connection.execute(AddConstraint(constraint))
        actual_fks.add(expected)
        print(f"[OK] FK añadida: {table.name}.{constraint.name}.")


def _verify_table(model) -> bool:
    table = model.__table__
    table_name = table.name
    inspector = inspect(db.engine)
    actual_info = {item["name"]: item for item in inspector.get_columns(table_name)}
    expected = {column.name: column for column in table.columns}
    missing = set(expected) - set(actual_info)
    columns_ok = not missing
    print(
        f"[{'OK' if columns_ok else 'ERROR'}] {table_name}: "
        f"columnas canónicas presentes; faltantes={sorted(missing)}."
    )

    types_ok = True
    nullability_ok = True
    for name in sorted(set(expected) & set(actual_info)):
        compatible = _type_compatible(actual_info[name]["type"], expected[name])
        types_ok = types_ok and compatible
        if not compatible:
            print(
                f"[ERROR] Tipo {table_name}.{name}: "
                f"actual={actual_info[name]['type']} "
                f"esperado={_compile_type(expected[name])}."
            )
        nullable = bool(actual_info[name].get("nullable", True))
        expected_nullable = bool(expected[name].nullable)
        same_nullable = nullable == expected_nullable
        nullability_ok = nullability_ok and same_nullable
        if not same_nullable:
            print(
                f"[ERROR] Nullability {table_name}.{name}: "
                f"actual={nullable} esperado={expected_nullable}."
            )

    actual_pk = tuple(inspector.get_pk_constraint(table_name).get("constrained_columns") or ())
    expected_pk = tuple(column.name for column in table.primary_key.columns)
    pk_ok = actual_pk == expected_pk
    print(
        f"[{'OK' if pk_ok else 'ERROR'}] PK {table_name}: "
        f"actual={actual_pk} esperado={expected_pk}."
    )

    actual_indexes = _actual_indexes(table_name)
    expected_indexes = _expected_indexes(table)
    missing_indexes = expected_indexes - actual_indexes
    indexes_ok = not missing_indexes
    print(
        f"[{'OK' if indexes_ok else 'ERROR'}] Índices/UNIQUE {table_name}: "
        f"faltantes={sorted(missing_indexes)}."
    )

    actual_checks = _actual_checks(table_name)
    expected_checks = _expected_checks(table)
    missing_checks = set(expected_checks) - set(actual_checks)
    mismatched_checks = sorted(
        name
        for name in set(expected_checks) & set(actual_checks)
        if _check_signature(expected_checks[name])
        != _check_signature(actual_checks[name])
    )
    checks_ok = not missing_checks and not mismatched_checks
    print(
        f"[{'OK' if checks_ok else 'ERROR'}] CHECK {table_name}: "
        f"faltantes={sorted(missing_checks)}; incompatibles={mismatched_checks}."
    )

    actual_fks = _actual_foreign_keys(table_name)
    expected_fks = _expected_foreign_keys(table)
    missing_fks = expected_fks - actual_fks
    fks_ok = not missing_fks
    print(
        f"[{'OK' if fks_ok else 'ERROR'}] FK/ON DELETE {table_name}: "
        f"faltantes={sorted(missing_fks)}."
    )

    fk_types_ok = True
    reflected_tables: dict[str, dict[str, dict]] = {table_name: actual_info}
    for local_columns, target_table, target_columns, ondelete in sorted(
        expected_fks & actual_fks
    ):
        if target_table not in reflected_tables:
            reflected_tables[target_table] = {
                item["name"]: item
                for item in inspector.get_columns(target_table)
            }
        target_info = reflected_tables[target_table]
        for local_name, target_name in zip(local_columns, target_columns):
            local_signature = _reflected_type_signature(
                actual_info[local_name]["type"]
            )
            target_signature = _reflected_type_signature(
                target_info[target_name]["type"]
            )
            compatible = local_signature == target_signature
            fk_types_ok = fk_types_ok and compatible
            if not compatible:
                print(
                    f"[ERROR] Tipos FK {table_name}.{local_name} -> "
                    f"{target_table}.{target_name}: actual={local_signature} "
                    f"destino={target_signature}; ON DELETE={ondelete}."
                )
    print(
        f"[{'OK' if fk_types_ok else 'ERROR'}] Tipos de FK {table_name}."
    )

    engine_ok = not _is_mysql() or _mysql_engine(table_name) == "innodb"
    print(
        f"[{'OK' if engine_ok else 'ERROR'}] Motor {table_name}: "
        f"{_mysql_engine(table_name) if _is_mysql() else 'n/a'}"
    )
    return all(
        (
            columns_ok,
            types_ok,
            nullability_ok,
            pk_ok,
            indexes_ok,
            checks_ok,
            fks_ok,
            fk_types_ok,
            engine_ok,
        )
    )


def _verify_all() -> bool:
    existing = set(inspect(db.engine).get_table_names())
    missing = set(HISTORICAL_TABLES) - existing
    if missing:
        print(f"[ERROR] Faltan tablas históricas: {sorted(missing)}")
        return False
    tables_ok = all([_verify_table(model) for model in HISTORICAL_MODELS])
    triggers_ok = _verify_immutability_triggers()
    return tables_ok and triggers_ok


def main() -> int:
    app = create_app()
    with app.app_context():
        try:
            existing = set(inspect(db.engine).get_table_names())

            # Todo el preflight de tablas preexistentes ocurre antes del primer DDL.
            for model in HISTORICAL_MODELS:
                if model.__tablename__ in existing:
                    _preflight_existing_table(model)
            print("[OK] Preflight histórico completado sin cambios destructivos.")

            before = _operational_snapshot()
            print(
                f"[OK] Snapshot operativo inicial: {len(before)} tablas, "
                f"{sum(int(item['count']) for item in before.values())} filas."
            )

            with db.engine.begin() as connection:
                for model in HISTORICAL_MODELS:
                    table_name = model.__tablename__
                    if table_name in existing:
                        if _is_mysql():
                            _upgrade_existing_table(connection, model)
                        else:
                            print(f"[OK] {table_name} ya es canónica; no se altera.")
                    else:
                        model.__table__.create(bind=connection, checkfirst=True)
                        existing.add(table_name)
                        print(f"[OK] Tabla histórica creada: {table_name}.")

                for model in HISTORICAL_MODELS:
                    _ensure_indexes(connection, model)
                    _ensure_constraints(connection, model)
                _ensure_immutability_triggers(connection)

            if not _verify_all():
                raise RuntimeError(
                    "La verificación fuerte del esquema histórico falló."
                )

            after = _operational_snapshot()
            if before != after:
                raise RuntimeError(
                    "El snapshot operativo cambió durante la migración."
                )
            print("[OK] Snapshot operativo final idéntico al inicial.")
            print("[OK] Migración histórica completada y verificada.")
            return 0
        except Exception as exc:
            db.session.rollback()
            # MySQL hace autocommit de DDL. No se intenta compensar eliminando
            # tablas/columnas: una reejecución idempotente continúa el proceso.
            print(f"[ERROR] Migración histórica fallida de forma segura: {exc}")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
