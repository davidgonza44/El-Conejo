"""Authorized thesis backup and read-only PRE/POST evidence. Never applies DDL/DML.

Only this script's newly created run directory is read/written. Dumps and row
values are never printed. Every table is hashed to detect unrelated row changes.
Credentials come only from the process environment, never from dotenv files.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from sqlalchemy import event, inspect, text
from migrate_movement_traceability import CHECKS, COLUMNS, _normalized, _validate_columns
from migrate_purchase_orders import engine_for

DATABASE = "ferreteria_conejo_tesis"
BACKUPS = Path(__file__).resolve().parents[1] / "instance" / "backups"
DUMP_PROGRAM = Path(r"C:\xampp\mysql\bin\mysqldump.exe")
REQUIRED_TABLES = (
    "stock_movements", "products", "sales", "sale_lines", "purchase_orders",
    "purchase_receptions", "purchase_reception_lines", "reconciliation_runs",
    "reconciliation_lines", "product_state_histories", "product_suppliers", "seed_manifests",
)
BACKUP_MARKERS = ("stock_movements", "products", "sales", "purchase_receptions",
                  "reconciliation_runs", "product_suppliers")


def json_value(value):
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    return str(value)


def save_json(path, value):
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2, default=json_value)
        output.write("\n")


def readonly_engine():
    engine = engine_for(DATABASE)
    protect_readonly(engine)
    return engine


def protect_readonly(engine):
    """Install database-level read-only transactions plus a fail-closed SQL guard."""
    if engine.url.database != DATABASE or engine.url.host not in ("localhost", "127.0.0.1", "::1"):
        raise RuntimeError("Only the exact authorized local database is allowed.")

    @event.listens_for(engine, "connect")
    def readonly(connection, _record):
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")

    @event.listens_for(engine, "before_cursor_execute")
    def read_guard(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().split(None, 1)[0].upper() not in {"SELECT", "SHOW", "DESCRIBE"}:
            raise RuntimeError("Non-read SQL blocked by thesis audit.")


def dump_database(engine, directory):
    if not DUMP_PROGRAM.is_file():
        raise RuntimeError("Expected local mysqldump executable is unavailable.")
    target = directory / (DATABASE + ".sql")
    environment = os.environ.copy()
    environment["MYSQL_PWD"] = engine.url.password or ""
    args = [str(DUMP_PROGRAM), "--no-defaults", "--protocol=TCP",
            "--host=" + engine.url.host, "--port=" + str(engine.url.port or 3306),
            "--user=" + (engine.url.username or "root"), "--single-transaction", "--quick",
            "--routines", "--triggers", "--events", "--hex-blob",
            "--default-character-set=utf8mb4", "--databases", DATABASE]
    with target.open("xb") as output:
        result = subprocess.run(args, stdout=output, stderr=subprocess.PIPE, env=environment, check=False)
    if result.returncode != 0 or target.stat().st_size == 0:
        raise RuntimeError("Backup failed or empty; migration must not proceed.")
    digest = hashlib.sha256()
    found = set()
    completed = False
    with target.open("rb") as source:
        for line in source:
            digest.update(line)
            for table in BACKUP_MARKERS:
                if ("CREATE TABLE `" + table + "`").encode() in line:
                    found.add(table)
            completed = completed or line.startswith(b"-- Dump completed on")
    if found != set(BACKUP_MARKERS) or not completed:
        raise RuntimeError("Backup is missing required structure/completion markers.")
    result = {"file": str(target), "exit_code": result.returncode, "bytes": target.stat().st_size,
              "sha256": digest.hexdigest(), "required_tables_verified": sorted(found),
              "includes": ["structure", "data", "triggers", "routines", "events"],
              "completed": completed}
    save_json(directory / "backup-verification.json", result)
    print(json.dumps(result))


def table_fingerprint(connection, inspector, table, columns=None):
    names = columns or [column["name"] for column in inspector.get_columns(table)]
    quote = connection.dialect.identifier_preparer.quote
    keys = inspector.get_pk_constraint(table)["constrained_columns"] or names
    sql = "SELECT " + ",".join(map(quote, names)) + " FROM " + quote(table)
    sql += " ORDER BY " + ",".join(map(quote, keys))
    digest = hashlib.sha256()
    count = 0
    for row in connection.execute(text(sql)):
        encoded = json.dumps(list(row), ensure_ascii=False, separators=(",", ":"), default=json_value)
        digest.update(encoded.encode("utf-8") + b"\n")
        count += 1
    return {"count": count, "sha256": digest.hexdigest(), "columns": names}


def snapshot(engine, before=None):
    with engine.connect() as connection:
        assert connection.execute(text("SELECT DATABASE()")).scalar_one() == DATABASE
        inspector = inspect(connection)
        tables = inspector.get_table_names()
        if not set(REQUIRED_TABLES).issubset(tables):
            raise RuntimeError("Required business tables are missing.")
        columns = {column["name"]: column for column in inspector.get_columns("stock_movements")}
        if before is None and set(COLUMNS).intersection(columns):
            raise RuntimeError("PRE requires both traceability columns to be absent.")
        fingerprints = {}
        for table in sorted(tables):
            old_columns = before["tables"][table]["columns"] if before and table in before["tables"] else None
            fingerprints[table] = table_fingerprint(connection, inspector, table, old_columns)
        metrics = dict(connection.execute(text(
            "SELECT MIN(id) min_id, MAX(id) max_id, SUM(quantity) sum_quantity, "
            "SUM(previous_stock) sum_previous_stock, SUM(new_stock) sum_new_stock FROM stock_movements"
        )).mappings().one())
        metrics["sum_current_stock"] = connection.execute(text("SELECT SUM(current_stock) FROM products")).scalar()
        metrics["movement_types"] = [dict(row) for row in connection.execute(text(
            "SELECT movement_type, COUNT(*) count, SUM(quantity) quantity FROM stock_movements "
            "GROUP BY movement_type ORDER BY movement_type")).mappings()]
        object_counts = {}
        for table, schema_column in (("TRIGGERS", "TRIGGER_SCHEMA"), ("ROUTINES", "ROUTINE_SCHEMA"),
                                     ("EVENTS", "EVENT_SCHEMA")):
            object_counts[table.lower()] = connection.execute(text(
                f"SELECT COUNT(*) FROM information_schema.{table} WHERE {schema_column}=:db"
            ), {"db": DATABASE}).scalar_one()
        result = {"database": DATABASE, "utc": datetime.now(timezone.utc).isoformat(),
                  "tables": fingerprints, "metrics": metrics, "database_objects": object_counts,
                  "stock_movements_schema": connection.execute(text("SHOW CREATE TABLE stock_movements")).one()[1]}
        if before:
            result["nonnull"] = dict(connection.execute(text(
                "SELECT COUNT(creation_origin) creation_origin, COUNT(external_reference) external_reference "
                "FROM stock_movements")).mappings().one())
            _validate_columns(columns)
            assert set(columns) == set(before["tables"]["stock_movements"]["columns"]) | set(COLUMNS)
            checks = {item["name"]: item["sqltext"] for item in inspector.get_check_constraints("stock_movements")}
            assert all(_normalized(checks[name]) == _normalized(sql) for name, sql in CHECKS.items())
        return result


def original_schema(schema):
    lines = [line for line in schema.splitlines()
             if not any("`" + name + "`" in line for name in (*COLUMNS, *CHECKS))]
    return "\n".join(line.rstrip().rstrip(",") for line in lines)


def compare(before, after):
    # JSON normalizes database Decimal values exactly as persisted in PRE.
    after = json.loads(json.dumps(after, default=json_value))
    assert before["tables"] == after["tables"], "Business rows/table inventory changed"
    assert before["metrics"] == after["metrics"], "Business totals changed"
    assert before["database_objects"] == after["database_objects"], "Stored objects changed"
    assert original_schema(before["stock_movements_schema"]) == original_schema(after["stock_movements_schema"]), "Other movement schema changed"
    assert after["nonnull"] == {"creation_origin": 0, "external_reference": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("backup-pre", "post", "after-smoke"))
    parser.add_argument("--run-directory", type=Path)
    args = parser.parse_args()
    engine = readonly_engine()
    try:
        if args.phase == "backup-pre":
            directory = BACKUPS / ("traceability_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3))
            directory.mkdir(parents=True, exist_ok=False)
            dump_database(engine, directory)
            value = snapshot(engine)
            save_json(directory / "PRE.json", value)
        else:
            if args.run_directory is None:
                parser.error("POST requires --run-directory from this run")
            directory = args.run_directory.resolve()
            if directory.parent != BACKUPS.resolve() or not directory.name.startswith("traceability_"):
                raise RuntimeError("Only the explicitly named traceability backup directory is allowed.")
            before = json.loads((directory / "PRE.json").read_text(encoding="utf-8"))
            value = snapshot(engine, before)
            save_json(directory / (args.phase.upper() + ".json"), value)
            compare(before, value)
            print("PASS: every original row hash/count and business total unchanged; authorized schema only; both non-NULL counts zero.")
        print("Evidence directory:", directory)
        print(json.dumps({"counts": {name: value["tables"][name]["count"] for name in REQUIRED_TABLES},
                          "metrics": value["metrics"], "database_objects": value["database_objects"],
                          "tables_fingerprinted": len(value["tables"])}, default=json_value))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
