"""Prepara solo las tres tablas 2026; sin Alembic ni conexiones por defecto."""
import argparse
import os
from pathlib import Path
import re
import sys
import tempfile

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import Boolean, CheckConstraint, JSON, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.dialects.mysql import LONGTEXT, TINYINT
from sqlalchemy.engine import URL

TABLE_NAMES = ("historical_2026_imports", "historical_2026_products", "historical_2026_daily")


def _tables():
    from app.models.real_2026 import Real2026Daily, Real2026Import, Real2026Product

    return tuple(model.__table__ for model in (Real2026Import, Real2026Product, Real2026Daily))


def _normal_check(value):
    value = re.sub(r"\s+", "", str(value)).replace("`", "").lower()
    while value.startswith("(") and value.endswith(")"):
        depth = 0
        for character in value[:-1]:
            depth += (character == "(") - (character == ")")
            if depth == 0:
                return value
        value = value[1:-1]
    return value


def _check_present(check, actual):
    expected = _normal_check(check.sqltext)
    allowed = {expected}
    if check.name == "ck_real2026_confirmed_slot":
        # MariaDB removes these two redundant AND-group parentheses because
        # AND binds before OR. Accept only that exact equivalent rendering.
        allowed.add(expected.replace("(", "").replace(")", ""))
    return bool(allowed & actual)


def _verify_columns(columns, table, checks):
    for column in table.columns:
        actual = columns[column.name]
        kind = actual["type"]
        maria_json = (isinstance(column.type, JSON) and isinstance(kind, LONGTEXT)
                      and any(_normal_check(item["sqltext"]) == f"json_valid({column.name})" for item in checks))
        maria_boolean = isinstance(column.type, Boolean) and isinstance(kind, TINYINT) and kind.display_width == 1
        if ((not maria_json and not maria_boolean and kind._type_affinity is not column.type._type_affinity)
                or actual["nullable"] != column.nullable):
            raise RuntimeError(f"Esquema 2026 incompatible en {table.name}.{column.name}: "
                f"tipo esperado {column.type}, observado {kind}; "
                f"nullable esperado {column.nullable}, observado {actual['nullable']}.")
        for attribute in ("length", "precision", "scale"):
            if getattr(kind, attribute, None) != getattr(column.type, attribute, None):
                raise RuntimeError("La precisión del esquema histórico 2026 existente es incompatible.")


def _verify_keys(inspector, table):
    if inspector.get_pk_constraint(table.name)["constrained_columns"] != [column.name for column in table.primary_key]:
        raise RuntimeError("La clave primaria histórica 2026 es incompatible.")
    actual_unique = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(table.name)}
    actual_unique.update(tuple(item["column_names"]) for item in inspector.get_indexes(table.name) if item["unique"])
    expected_unique = {tuple(column.name for column in item.columns)
                       for item in table.constraints if isinstance(item, UniqueConstraint)}
    if not expected_unique <= actual_unique:
        raise RuntimeError("Faltan restricciones de unicidad del histórico 2026.")
    actual_indexes = {tuple(item["column_names"]) for item in inspector.get_indexes(table.name)}
    if not {tuple(column.name for column in item.columns) for item in table.indexes} <= actual_indexes:
        raise RuntimeError("Faltan índices de consulta del histórico 2026.")


def _verify_foreign_keys(connection, inspector, table):
    foreign_keys = inspector.get_foreign_keys(table.name)
    if any(item.get("referred_schema") not in (None, connection.engine.url.database)
           or any(item.get("options", {}).get(action, "RESTRICT").upper() not in ("RESTRICT", "NO ACTION")
                  for action in ("ondelete", "onupdate")) for item in foreign_keys):
        raise RuntimeError("Las relaciones históricas 2026 no permiten cascadas ni otra base.")
    actual_fk = {(tuple(item["constrained_columns"]), item["referred_table"], tuple(item["referred_columns"]))
                 for item in foreign_keys}
    expected_fk = {(tuple(column.name for column in item.columns), item.referred_table.name,
                    tuple(element.column.name for element in item.elements))
                   for item in table.foreign_key_constraints}
    if actual_fk != expected_fk:
        raise RuntimeError("Las relaciones del histórico 2026 existente son incompatibles.")


def _verify_storage(connection, table):
    if connection.dialect.name == "sqlite":
        return
    storage = connection.execute(text("SELECT ENGINE,TABLE_COLLATION FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:name"), {"name": table.name}).one()
    if storage.ENGINE != "InnoDB" or not storage.TABLE_COLLATION.startswith("utf8mb4_"):
        raise RuntimeError("El histórico 2026 requiere InnoDB y utf8mb4.")
    if connection.execute(text("SELECT COUNT(*) FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:name AND SUB_PART IS NOT NULL"),
            {"name": table.name}).scalar_one():
        raise RuntimeError("Los índices históricos 2026 deben cubrir las columnas completas.")


def _checks(connection, inspector, table):
    if connection.dialect.name == "sqlite":
        return inspector.get_check_constraints(table.name)
    # MariaDB represents JSON as LONGTEXT with an inline json_valid CHECK.
    # SQLAlchemy's SHOW CREATE parser omits inline checks, so read the server's
    # constraint registry rather than accepting unvalidated LONGTEXT.
    return [dict(name=row.CONSTRAINT_NAME, sqltext=row.CHECK_CLAUSE)
            for row in connection.execute(text(
                "SELECT CONSTRAINT_NAME,CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS "
                "WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=:table"), {"table": table.name})]


def _verify(connection, table):
    inspector = inspect(connection)
    columns = {column["name"]: column for column in inspector.get_columns(table.name)}
    if set(columns) != set(table.columns.keys()):
        raise RuntimeError("El esquema histórico 2026 existente es incompatible; requiere revisión.")
    checks = _checks(connection, inspector, table)
    _verify_columns(columns, table, checks)
    _verify_keys(inspector, table)
    _verify_foreign_keys(connection, inspector, table)
    actual_checks = {_normal_check(item["sqltext"]) for item in checks}
    if any(not _check_present(item, actual_checks) for item in table.constraints if isinstance(item, CheckConstraint)):
        raise RuntimeError("Faltan restricciones de validación del histórico 2026.")
    _verify_storage(connection, table)


def validate_isolated_mariadb(connection):
    """Fail closed: only this task's separately identified disposable Docker DB."""
    values = {name: os.environ.get(name, "") for name in (
        "CONEJO_CONTAINER", "DOCKER_ENV_KIND", "REAL2026_ISOLATED", "DB_HOST", "DB_NAME", "ENVIRONMENT_ID")}
    if (values["CONEJO_CONTAINER"] != "1" or values["DOCKER_ENV_KIND"] != "disposable"
            or values["REAL2026_ISOLATED"] != "1" or values["DB_HOST"] != "db"
            or not re.fullmatch(r"conejo_disposable_real2026_[0-9a-f]{8}", values["DB_NAME"])
            or not re.fullmatch(r"[0-9a-f]{32}", values["ENVIRONMENT_ID"])):
        raise RuntimeError("El esquema MariaDB 2026 exige un laboratorio Docker desechable identificado.")
    if (connection.dialect.name not in ("mysql", "mariadb") or not connection.dialect.is_mariadb
            or connection.engine.url.host != "db" or connection.engine.url.database != values["DB_NAME"]):
        raise RuntimeError("El destino no es la MariaDB privada del laboratorio 2026.")
    if connection.execute(text("SELECT DATABASE()")).scalar_one() != values["DB_NAME"]:
        raise RuntimeError("La base conectada no coincide con la identidad del laboratorio.")
    if connection.execute(text("SELECT @@session.check_constraint_checks, @@session.foreign_key_checks")).one() != (1, 1):
        raise RuntimeError("Las restricciones MariaDB deben estar habilitadas.")
    if not inspect(connection).has_table("real2026_lab_installation"):
        raise RuntimeError("Falta la marca privada del laboratorio 2026.")
    identity = connection.execute(text(
        "SELECT environment_id,kind FROM real2026_lab_installation WHERE id=1")).one()
    if identity.environment_id != values["ENVIRONMENT_ID"] or identity.kind != "disposable":
        raise RuntimeError("La base pertenece a otro entorno; no se permite modificarla.")


def apply_changes(connection):
    """DDL aditivo e idempotente; el llamador valida el destino aislado."""
    if connection.dialect.name != "sqlite":
        validate_isolated_mariadb(connection)
    inspector = inspect(connection)
    if not inspector.has_table("users"):
        raise RuntimeError("Se requiere una tabla users existente para la auditoría de responsables.")
    tables = _tables()
    for table in tables:
        if inspect(connection).has_table(table.name):
            _verify(connection, table)
    for table in tables:
        table.create(connection, checkfirst=True)
        _verify(connection, table)


def _isolated_target(value, root_value):
    root = Path(root_value).resolve()
    target = Path(value).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    workspace_tmp = Path(__file__).resolve().parents[1] / ".tmp"
    allowed_root = root.is_relative_to(temp_root) or root.is_relative_to(workspace_tmp)
    if not allowed_root or root in (temp_root, workspace_tmp) or not target.is_relative_to(root):
        raise RuntimeError("El destino debe estar dentro de una carpeta temporal aislada explícita.")
    if not re.fullmatch(r"real2026_[A-Za-z0-9_-]+\.sqlite", target.name) or target.is_symlink():
        raise RuntimeError("El archivo SQLite aislado debe llamarse real2026_*.sqlite.")
    if not target.is_file():
        raise RuntimeError("La base SQLite temporal debe existir; no se crean usuarios ni bases operativas.")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated-sqlite")
    parser.add_argument("--workspace")
    args = parser.parse_args()
    if not args.isolated_sqlite:
        if args.workspace:
            parser.error("--workspace solo se usa con --isolated-sqlite")
        print("PLAN: crear solo historical_2026_imports/products/daily; sin conexión ni migraciones previas.")
        return
    if not args.workspace:
        parser.error("--isolated-sqlite requiere --workspace temporal explícito")
    target = _isolated_target(args.isolated_sqlite, args.workspace)
    engine = create_engine(URL.create("sqlite", database=str(target)))
    try:
        with engine.begin() as connection:
            apply_changes(connection)
    finally:
        engine.dispose()
    print("PASS: esquema 2026 aislado creado/verificado; tablas anteriores preservadas.")


if __name__ == "__main__":
    main()
