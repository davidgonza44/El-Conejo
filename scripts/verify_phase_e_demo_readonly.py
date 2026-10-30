"""Run via stdin in the existing DEMO app; only SELECTs in a READ ONLY transaction.

Returns aggregates/hashes, never rows, secrets, filenames or personal information.
"""
import hashlib
import json
from pathlib import Path

from sqlalchemy import inspect, text
from docker_support.bootstrap import engine
from docker_support.guard import validate_demo


def main():
    assert validate_demo() == "conejo_demo_8b867d4b"
    target = engine()
    with target.connect() as connection:
        assert connection.execute(text("SELECT DATABASE()")).scalar_one() == "conejo_demo_8b867d4b"
        connection.rollback()
        connection.exec_driver_sql("START TRANSACTION READ ONLY, WITH CONSISTENT SNAPSHOT")
        inspector = inspect(connection)
        tables = sorted(inspector.get_table_names())
        result = {"database": "conejo_demo_8b867d4b", "transaction": "READ ONLY",
                  "revision": connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one(),
                  "tables": {}}
        # Hash business rows in SQL; no row contents leave the server. Credential,
        # session, personal and configuration tables are deliberately excluded.
        protected = ("products", "product_suppliers", "stock_movements", "purchase_orders", "purchase_order_lines",
                     "purchase_receptions", "purchase_reception_lines", "quotes", "quote_lines",
                     "supplier_quotes", "supplier_quote_lines", "sales_quotes", "sales_quote_lines",
                     "delivery_notes", "delivery_note_items", "historical_imports", "historical_demand_records",
                     "inventory_snapshots", "reconciliation_cuts", "reconciliation_cut_stock",
                     "reconciliation_runs", "reconciliation_lines", "sales", "sale_lines",
                     "roles", "permissions", "role_permissions", "demo_seed")
        for name in protected:
            if name not in tables:
                result["tables"][name] = None
                continue
            columns = inspector.get_columns(name)
            parts = [f"COALESCE(HEX(`{column['name']}`),'NULL')" for column in columns]
            row_hash = "SHA2(CONCAT_WS('|'," + ",".join(parts) + "),256)"
            hashes = connection.exec_driver_sql(f"SELECT {row_hash} FROM `{name}` ORDER BY {row_hash}").scalars()
            digest, count = hashlib.sha256(), 0
            for hashed in hashes:
                digest.update(hashed.encode("ascii"))
                count += 1
            result["tables"][name] = {"count": count, "sha256": digest.hexdigest()}
        # Only authorization/account flags, never passwords, identities or tokens.
        rows = connection.exec_driver_sql("SELECT id,role,is_active FROM users ORDER BY id").all()
        result["account_flags_sha256"] = hashlib.sha256(repr(rows).encode()).hexdigest()
        result["catalog_amount_fields"] = connection.exec_driver_sql(
            "SELECT 2*(SELECT COUNT(*) FROM products)+(SELECT COUNT(*) FROM product_suppliers)").scalar()
        result["catalog_currency_counts"] = dict(zip(('sale','purchase','supplier'), connection.exec_driver_sql(
            "SELECT (SELECT COUNT(*) FROM products WHERE sale_price_currency IS NOT NULL),"
            "(SELECT COUNT(*) FROM products WHERE purchase_price_currency IS NOT NULL),"
            "(SELECT COUNT(*) FROM product_suppliers WHERE purchase_price_currency IS NOT NULL)").one()))
        result["reconciliation_tables_present"] = all(name in tables for name in
            ("reconciliation_cuts", "reconciliation_cut_stock", "reconciliation_runs", "reconciliation_lines"))
        connection.rollback()
    source = Path("docker_support/scheduler.py").read_text(encoding="utf-8")
    result["scheduler_source_sha256"] = hashlib.sha256(source.encode()).hexdigest()
    result["scheduler_calls_reconciliation"] = "reconciliation" in source
    result["scheduler_calls_backup"] = "run_scheduled_backup" in source
    result["delivery_security_source_sha256"] = {
        name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in
        ("app/routes/delivery_notes.py", "app/services/delivery_note_service.py", "app/models/delivery_note.py")}
    from app import create_app
    from app.services import reconciliation_automation_service as auto
    app = create_app()
    result["cut_times"] = [value.strftime("%H:%M") for value in auto.TIMES.values()]
    result["timezone"] = str(auto.CARACAS)
    result["activation_configured"] = bool(app.config.get("RECONCILIATION_ACTIVATED_ON"))
    result["landing_exists"] = (Path(app.instance_path) / "reconciliation_landing").exists()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
