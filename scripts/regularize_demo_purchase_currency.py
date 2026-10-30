"""Approved, audited USD/VES regularization on the exact DEMO 5001 only.

Requires a verified isolated restore, an unchanged baseline and 22 explicit
administrative decisions. DDL stages never guess a denomination. Rerunning the
same manifest before new business operations verifies without repeating writes.
"""
import argparse
import csv
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docker_support.bootstrap import engine, identity
from docker_support.demo import require_ready
from docker_support.guard import validate_demo, validate_demo_url

DATABASE = "conejo_demo_8b867d4b"
SOURCE_SHA256 = "027a7f566025334a6c04f1888a673d327aa5cdd422ac60bc6d577ce35fd2e0ee"
FIRST = "0004_inventory_adjustments"
STAGED = "0005_purchase_currency"
FINAL = "0006_purchase_currency_required"
EXPECTED = {key: f"OC-{key:06d}" for key in range(1, 23)}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def verify_approval_manifest(manifest, approved_csv):
    if manifest.get("database") != DATABASE or manifest.get("port") != 5001:
        raise RuntimeError("Approval target mismatch.")
    if manifest.get("source_csv_sha256") != SOURCE_SHA256 or manifest.get("approved_csv_sha256") != sha256(approved_csv):
        raise RuntimeError("Approved CSV hash mismatch.")
    expected_decisions = [{"oc_id": key, "oc_codigo": code, "currency": "VES",
                           "evidence_type": "asignacion_administrativa_sintetica", "approved": True}
                          for key, code in EXPECTED.items()]
    if manifest.get("decisions") != expected_decisions or manifest.get("phase_b_authorized") is not True:
        raise RuntimeError("Approval is missing or differs from the 22 authorized VES assignments.")
    if not manifest.get("approved_by") or manifest.get("approval_date") != "2026-10-08":
        raise RuntimeError("Approval attribution is required.")
    return expected_decisions


def verify_csv_decisions(source, approved, expected_decisions):
    if len(source) != 22 or len(approved) != 22:
        raise RuntimeError("Exactly 22 individually approved decisions required.")
    for original, decision, expected in zip(source, approved, expected_decisions):
        if ({key: value for key, value in decision.items() if key != "decision_usuario"}
                != {key: value for key, value in original.items() if key != "decision_usuario"}):
            raise RuntimeError("Approved CSV changed historical fields.")
        if (int(decision["oc_id"]) != expected["oc_id"] or decision["oc_codigo"] != expected["oc_codigo"]
                or decision["moneda_actual"] or decision["moneda_propuesta"] != "VES"
                or decision["tipo_evidencia"] != expected["evidence_type"]
                or decision["decision_usuario"] != "APROBADO VES — autorización explícita Fase B 2026-10-08"):
            raise RuntimeError("An individual CSV decision is missing or mismatched.")


def verify_restore_proof(proof, manifest):
    if (proof.get("database") != DATABASE or proof.get("port") != 5001
            or proof.get("all_row_sha256_and_schema_hashes_match") is not True
            or proof.get("ports_published") is not False or proof.get("network_internal") is not True
            or proof.get("sha256") != manifest.get("backup_sha256")):
        raise RuntimeError("Verified isolated restore proof required.")


def approved_inputs(args):
    if sha256(args.source_csv) != SOURCE_SHA256:
        raise RuntimeError("Phase A source CSV changed; operation suspended.")
    source, approved = read_csv(args.source_csv), read_csv(args.approved_csv)
    manifest = read_json(args.manifest)
    expected_decisions = verify_approval_manifest(manifest, args.approved_csv)
    verify_csv_decisions(source, approved, expected_decisions)
    verify_restore_proof(read_json(args.restore_proof), manifest)
    baseline = read_json(args.baseline)
    if baseline["tables"]["purchase_orders"]["count"] != 31 or baseline["alembic"] != FIRST:
        raise RuntimeError("Baseline differs from Phase A.")
    return approved, manifest, baseline


def verify_protected(connection, baseline, *, initial=False):
    for name, expected in baseline["tables"].items():
        if name == "alembic_version":
            continue
        columns = ",".join("`" + column + "`" for column in expected["columns"])
        rows = [tuple(row) for row in connection.execute(text("SELECT " + columns + " FROM `" + name + "`"))]
        rows.sort(key=lambda row: json.dumps(row, default=str))
        digest = hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()
        if len(rows) != expected["count"] or digest != expected["hash"]:
            raise RuntimeError("Protected rows changed: " + name)
        if initial or name != "purchase_orders":
            checksum = connection.execute(text("CHECKSUM TABLE `" + name + "`")).one()[1]
            if checksum != expected["checksum"]:
                raise RuntimeError("Protected checksum changed: " + name)


def verify_orders(connection):
    rows = connection.execute(text("SELECT id,order_number,currency,supplier_quote_id FROM purchase_orders ORDER BY id")).all()
    if len(rows) != 31 or any(row.currency != "VES" for row in rows):
        raise RuntimeError("Expected 31 VES orders, no missing/invalid currencies.")
    assigned = {row.id: row.order_number for row in rows if row.id in EXPECTED and row.supplier_quote_id is None}
    if assigned != EXPECTED:
        raise RuntimeError("The 22 legacy orders or their original quote links changed.")
    linked = connection.execute(text(
        "SELECT COUNT(*) FROM purchase_orders po JOIN supplier_quotes sq ON sq.id=po.supplier_quote_id "
        "WHERE po.id NOT BETWEEN 1 AND 22 AND po.currency=sq.currency AND sq.currency='VES'"
    )).scalar_one()
    if linked != 9:
        raise RuntimeError("The nine existing VES orders must retain their original quotation links.")


def verify_assignments(connection, manifest, manifest_hash):
    rows = connection.execute(text("SELECT * FROM purchase_currency_assignments ORDER BY purchase_order_id")).mappings().all()
    if len(rows) != 22:
        raise RuntimeError("Exactly 22 audit records required.")
    for row, (key, number) in zip(rows, EXPECTED.items()):
        expected = {"purchase_order_id": key, "order_number": number, "old_currency": None,
                    "assigned_currency": "VES", "evidence_type": "asignacion_administrativa_sintetica",
                    "source_csv_sha256": SOURCE_SHA256, "approved_manifest_sha256": manifest_hash,
                    "approved_by": manifest["approved_by"], "approval_date": date.fromisoformat(manifest["approval_date"])}
        if any(row[field] != value for field, value in expected.items()) or not row["executed_at"]:
            raise RuntimeError("Individual audit mismatch: " + number)


def assign(target, manifest, manifest_hash, baseline):
    with target.begin() as connection:
        rows = connection.execute(text("SELECT id,order_number,currency,supplier_quote_id FROM purchase_orders ORDER BY id FOR UPDATE")).all()
        unresolved = {row.id: row.order_number for row in rows if row.currency is None and row.supplier_quote_id is None}
        if unresolved != EXPECTED or len(rows) != 31:
            raise RuntimeError("Unresolved orders differ from the individually authorized set.")
        if connection.execute(text("SELECT COUNT(*) FROM purchase_currency_assignments")).scalar_one():
            raise RuntimeError("Unexpected preexisting currency decisions.")
        verify_protected(connection, baseline)
        executed = datetime.now(timezone.utc).replace(tzinfo=None)
        for key, number in EXPECTED.items():
            result = connection.execute(text(
                "UPDATE purchase_orders SET currency='VES' WHERE id=:id AND order_number=:number "
                "AND currency IS NULL AND supplier_quote_id IS NULL"), {"id": key, "number": number})
            if result.rowcount != 1:
                raise RuntimeError("Assignment did not affect exactly one order.")
            connection.execute(text(
                "INSERT INTO purchase_currency_assignments(purchase_order_id,order_number,old_currency,assigned_currency,"
                "evidence_type,source_csv_sha256,approved_manifest_sha256,approved_by,approval_date,executed_at) "
                "VALUES(:id,:number,NULL,'VES','asignacion_administrativa_sintetica',:source,:manifest,:actor,:date,:executed)"),
                {"id": key, "number": number, "source": SOURCE_SHA256, "manifest": manifest_hash,
                 "actor": manifest["approved_by"], "date": manifest["approval_date"], "executed": executed})
        verify_orders(connection)
        verify_assignments(connection, manifest, manifest_hash)
        verify_protected(connection, baseline)


def verify_required(connection):
    column = next(column for column in inspect(connection).get_columns("purchase_orders") if column["name"] == "currency")
    if column["nullable"] or column["default"] is not None or column["type"].length != 8:
        raise RuntimeError("Currency must be VARCHAR(8) NOT NULL without any default.")
    constraints = {row["name"]: row["sqltext"] for row in inspect(connection).get_check_constraints("purchase_orders")}
    if "ck_po_currency" not in constraints or "USD" not in constraints["ck_po_currency"] or "VES" not in constraints["ck_po_currency"]:
        raise RuntimeError("USD/VES CHECK constraint missing.")


def main(args):
    if validate_demo() != DATABASE:
        raise RuntimeError("Only the individually authorized DEMO is allowed.")
    _, manifest, baseline = approved_inputs(args)
    manifest_hash = sha256(args.manifest)
    target = engine()
    validate_demo_url(target.url)
    with target.connect() as connection:
        identity(connection)
        require_ready(connection)
        if connection.execute(text("SELECT DATABASE()")).scalar_one() != DATABASE:
            raise RuntimeError("Database mismatch.")
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if revision not in (FIRST, FINAL):
            raise RuntimeError("Unexpected or incomplete migration state; manual recovery review required.")
        verify_protected(connection, baseline, initial=revision == FIRST)
    if revision == FIRST:
        command.upgrade(Config("alembic.ini"), STAGED)
        assign(target, manifest, manifest_hash, baseline)
        command.upgrade(Config("alembic.ini"), FINAL)
    with target.connect() as connection:
        verify_orders(connection)
        verify_assignments(connection, manifest, manifest_hash)
        verify_required(connection)
        verify_protected(connection, baseline)
    target.dispose()
    print(json.dumps({"database": DATABASE, "port": 5001, "revision": FINAL, "orders": 31,
                      "VES": 31, "missing": 0, "invalid": 0, "assignments": 22,
                      "protected_rows_unchanged": True, "idempotent_rerun": revision == FINAL,
                      "manifest_sha256": manifest_hash}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-csv", "approved-csv", "manifest", "baseline", "restore-proof"):
        parser.add_argument("--" + name, required=True, type=Path)
    try:
        main(parser.parse_args())
    except Exception as error:
        print("SUSPENDED: " + type(error).__name__ + "; no automatic recovery.", file=sys.stderr)
        if isinstance(error, RuntimeError):
            print(str(error), file=sys.stderr)
        raise SystemExit(1)
