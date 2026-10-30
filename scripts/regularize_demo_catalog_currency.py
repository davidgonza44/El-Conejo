"""154 individually approved synthetic USD denominations; exact DEMO only.

No DDL, conversion, numeric update or guessed currency. Requires a matching
pre-migration baseline and a verified, isolated restore of the same backup.
Manifest generation and execution are separate operations.
"""
import argparse
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docker_support.bootstrap import engine, identity
from docker_support.demo import require_ready
from docker_support.guard import validate_demo

DATABASE = "conejo_demo_8b867d4b"
FIRST = "0006_purchase_currency_required"
FINAL = "0007_catalog_currency"
APPROVAL = "USR-FASE-C-20261009-CATALOGO-USD-154"
EVIDENCE = "asignacion_administrativa_sintetica"
REASON = "USD autorizado individualmente para DEMO; no recupera moneda historica ni convierte importes."
FIELDS = {"products": ("sale_price_currency", "purchase_price_currency"),
          "product_suppliers": ("purchase_price_currency",)}
EXPECTED = {(table, key, field) for table, fields in FIELDS.items()
            for key in range(1, 53 if table == "products" else 51) for field in fields}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(value):
    return hashlib.sha256(json.dumps(value, default=str, separators=(",", ":")).encode()).hexdigest()


def verify_identity(connection):
    if validate_demo() != DATABASE or connection.execute(text("SELECT DATABASE()")).scalar_one() != DATABASE:
        raise RuntimeError("DEMO database identity mismatch.")
    registry = identity(connection)
    if not registry.ready or registry.environment_id != "8b867d4bbfce4447bf646e1b56330d75":
        raise RuntimeError("DEMO installation identity mismatch.")
    require_ready(connection)


def verify_inputs(baseline, proof):
    if (baseline.get("database") != DATABASE or baseline.get("port") != 5001 or baseline.get("revision") != FIRST
            or proof.get("database") != DATABASE or proof.get("port") != 5001
            or proof.get("all_row_and_schema_hashes_match") is not True
            or proof.get("network_internal") is not True or proof.get("ports_published") is not False
            or not proof.get("backup_sha256")):
        raise RuntimeError("Matching baseline and verified isolated restore required.")
    expected_counts = {"products": 52, "product_suppliers": 50, "purchase_orders": 32, "supplier_quotes": 11}
    if any(baseline["tables"][table]["count"] != count for table, count in expected_counts.items()):
        raise RuntimeError("Approved census counts differ.")


def verify_original_columns(connection, baseline):
    inspector = inspect(connection)
    if set(inspector.get_table_names()) != set(baseline["tables"]):
        raise RuntimeError("Unexpected tables; operation suspended.")
    for table, expected in baseline["tables"].items():
        if table == "alembic_version":
            continue
        actual = {col["name"] for col in inspector.get_columns(table)}
        if actual - set(FIELDS.get(table, ())) != set(expected["columns"]):
            raise RuntimeError("Unexpected original columns: " + table)
        columns = ",".join("`" + column + "`" for column in expected["columns"])
        rows = [list(row) for row in connection.execute(text("SELECT " + columns + " FROM `" + table + "`"))]
        rows.sort(key=lambda row: json.dumps(row, default=str))
        if len(rows) != expected["count"] or digest(rows) != expected["sha256"]:
            raise RuntimeError("Original row integrity mismatch: " + table)


def make_manifest(connection, baseline, proof):
    verify_identity(connection)
    if connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() != FIRST:
        raise RuntimeError("Manifest requires the unchanged pre-migration revision.")
    verify_original_columns(connection, baseline)
    decisions = []
    regularization_date = datetime.now(ZoneInfo("America/Caracas")).date().isoformat()
    for table, fields in FIELDS.items():
        values = [field.removesuffix("_currency") for field in fields]
        rows = connection.execute(text("SELECT id," + ",".join(values) + " FROM " + table + " ORDER BY id")).mappings()
        for row in rows:
            for field, price in zip(fields, values):
                decisions.append({"table": table, "id": row["id"], "currency_field": field,
                                  "original_numeric_value": str(row[price]), "previous_currency": None,
                                  "authorized_currency": "USD", "reason": REASON,
                                  "evidence_classification": EVIDENCE, "approval_id": APPROVAL,
                                  "regularization_date": regularization_date})
    if {(row["table"], row["id"], row["currency_field"]) for row in decisions} != EXPECTED or len(decisions) != 154:
        raise RuntimeError("Individual census differs from the 154 authorized decisions.")
    return {"database": DATABASE, "port": 5001, "approval_id": APPROVAL, "approval_date": "2026-10-09",
            "approved_by": "usuario: autorizacion expresa Fase C", "baseline_sha256": digest(baseline),
            "backup_sha256": proof["backup_sha256"], "decisions": decisions}


def verify_manifest(manifest, baseline, proof):
    if (manifest.get("database") != DATABASE or manifest.get("port") != 5001
            or manifest.get("approval_id") != APPROVAL or manifest.get("approval_date") != "2026-10-09"
            or manifest.get("baseline_sha256") != digest(baseline)
            or manifest.get("backup_sha256") != proof["backup_sha256"]):
        raise RuntimeError("Approval manifest identity or fingerprints differ.")
    decisions = manifest.get("decisions", [])
    keys = {(row["table"], row["id"], row["currency_field"]) for row in decisions}
    if keys != EXPECTED or len(decisions) != 154:
        raise RuntimeError("Exactly 154 individually authorized cases required.")
    for row in decisions:
        if (row["previous_currency"] is not None or row["authorized_currency"] != "USD"
                or row["reason"] != REASON or row["evidence_classification"] != EVIDENCE
                or row["approval_id"] != APPROVAL or not row["regularization_date"]):
            raise RuntimeError("Individual synthetic decision differs from authorization.")


def apply_manifest(connection, manifest, baseline):
    verify_identity(connection)
    if connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() != FINAL:
        raise RuntimeError("Schema migration must complete separately before regularization.")
    for table in FIELDS:
        connection.execute(text("SELECT id FROM " + table + " ORDER BY id FOR UPDATE")).all()
    verify_original_columns(connection, baseline)
    states = []
    for row in manifest["decisions"]:
        table, field = row["table"], row["currency_field"]
        price = field.removesuffix("_currency")
        current = connection.execute(text("SELECT " + price + "," + field + " FROM " + table + " WHERE id=:id"),
                                     {"id": row["id"]}).one()
        if Decimal(row["original_numeric_value"]) != current[0] or current[1] not in (None, "USD"):
            raise RuntimeError("Individual numeric or previous currency conflict.")
        states.append(current[1])
    if any(state is None for state in states) and any(state == "USD" for state in states):
        raise RuntimeError("Unexpected partially regularized state; no automatic repair.")
    changed = 0
    if all(state is None for state in states):
        for row in manifest["decisions"]:
            field = row["currency_field"]
            result = connection.execute(text("UPDATE " + row["table"] + " SET " + field + "=:currency "
                "WHERE id=:id AND " + field + " IS NULL AND " + field.removesuffix("_currency") + "=:price"),
                {"id": row["id"], "currency": "USD", "price": Decimal(row["original_numeric_value"])})
            if result.rowcount != 1:
                raise RuntimeError("Assignment must affect exactly one approved value.")
            changed += 1
    verify_original_columns(connection, baseline)
    for table, fields in FIELDS.items():
        for field in fields:
            unknown = connection.execute(text("SELECT COUNT(*) FROM " + table + " WHERE " + field +
                " IS NULL OR HEX(" + field + ") <> HEX('USD')")).scalar_one()
            if unknown:
                raise RuntimeError("Regularized set contains an unassigned denomination.")
    return {"database": DATABASE, "port": 5001, "revision": FINAL, "authorized_assignments": 154,
            "assignments_changed": changed, "idempotent_rerun": changed == 0,
            "original_columns_unchanged": True, "manifest_sha256": digest(manifest),
            "executed_at": datetime.now(ZoneInfo("America/Caracas")).isoformat()}


def main(args):
    baseline, proof = read_json(args.baseline), read_json(args.restore_proof)
    verify_inputs(baseline, proof)
    target = engine()
    if args.create_manifest:
        if args.manifest.exists():
            raise RuntimeError("Manifest already exists; never overwrite approval evidence.")
        with target.connect() as connection:
            connection.execute(text("SET SESSION TRANSACTION READ ONLY"))
            connection.commit()
            connection.execute(text("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY"))
            manifest = make_manifest(connection, baseline, proof)
        args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"manifest_created": True, "decisions": 154, "manifest_sha256": digest(manifest)}))
    else:
        manifest = read_json(args.manifest)
        verify_manifest(manifest, baseline, proof)
        with target.begin() as connection:
            result = apply_manifest(connection, manifest, baseline)
        print(json.dumps(result))
    target.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create-manifest", action="store_true")
    for argument in ("baseline", "restore-proof", "manifest"):
        parser.add_argument("--" + argument, type=Path, required=True)
    try:
        main(parser.parse_args())
    except Exception as error:
        print("SUSPENDED: " + type(error).__name__ + "; no automatic recovery.", file=sys.stderr)
        if isinstance(error, RuntimeError):
            print(str(error), file=sys.stderr)
        raise SystemExit(1)
