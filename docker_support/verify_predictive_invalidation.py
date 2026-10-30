"""Canonical DEMO demand invalidation in one uncommitted, rolled-back transaction.

Confirmed source quantities remain immutable. A proven synthetic DEMO record is
temporarily excluded through its mutable include_in_demand flag. No commit,
inventory write, sale, file import, HTTP request or application cache is used.
"""
import argparse
import json
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import load_only

from docker_support.guard import validate_demo, validate_demo_url


def guarded_app():
    if validate_demo() != "conejo_demo_8b867d4b":
        raise RuntimeError("Only the explicitly authorized DEMO database is allowed")
    from docker_support.bootstrap import engine, identity
    from docker_support.demo import require_ready

    target = engine()
    try:
        validate_demo_url(target.url)
        with target.connect() as connection:
            require_ready(connection)
            if not identity(connection).ready:
                raise RuntimeError("DEMO installation must be ready")
            if connection.execute(text("SELECT DATABASE()")).scalar_one() != "conejo_demo_8b867d4b":
                raise RuntimeError("Actual database mismatch")
    finally:
        target.dispose()
    from app import create_app

    return create_app()


def verify():
    app = guarded_app()
    from app.extensions import db
    from app.models import HistoricalDemandRecord as Record, HistoricalImport, Product
    from app.services import predictive_data_service as data, predictive_cache_service as cache
    from app.services import predictive_job_service as jobs
    from app.services.demand_analysis_service import institutional_today

    def fingerprint(series):
        return cache.digest((series.product_id, series.granularity, series.time_unit,
                             series.values, [day.isoformat() for day in series.starts]))

    def evaluate(series, state):
        specs = [(series, horizon, "mean") for horizon in (7, 30, 90)]
        return jobs.prediction_token(specs), jobs._compute(specs, state)

    with app.app_context():
        validate_demo_url(db.engine.url)
        today = institutional_today()
        state = cache._Predictions()  # Pure local cache; never the app extension.
        original = None
        try:
            row = (Record.query.options(load_only(Record.id, Record.historical_import_id,
                       Record.product_id, Record.include_in_demand, Record.lock_version))
                   .join(HistoricalImport, Record.historical_import_id == HistoricalImport.id)
                   .join(Product, Record.product_id == Product.id)
                   .filter(HistoricalImport.source_system == "DEMO-VISUAL-V2",
                           HistoricalImport.document_type == "DEMO-SYNTHETIC",
                           HistoricalImport.original_filename == "DEMO-v2-historico-2025.csv",
                           HistoricalImport.status == "confirmed",
                           Product.code.like("DEMO-%"), Product.is_active.is_(True),
                           Record.include_in_demand.is_(True), Record.record_type == "sale",
                           Record.effective_status.in_(("issued", "active")))
                   .order_by(Record.product_id, Record.id).first())
            if row is None:
                raise RuntimeError("No source record proven synthetic by DEMO import metadata")
            record_id, product_id, import_id = row.id, row.product_id, row.historical_import_id
            before = data.load_series(today=today, product_id=product_id)[product_id]
            original = fingerprint(before)
            original_token, before_blocks = evaluate(before, state)
            row.include_in_demand = False
            db.session.flush()  # Deliberately uncommitted; source facts are untouched.
            after = data.load_series(today=today, product_id=product_id)[product_id]
            assert fingerprint(after) != original
            changed_token, after_blocks = evaluate(after, state)
            assert changed_token != original_token
            assert all(before_blocks[(product_id, horizon)][0].total
                       != after_blocks[(product_id, horizon)][0].total for horizon in (7, 30, 90))
        finally:
            db.session.rollback()
        try:
            restored = data.load_series(today=today, product_id=product_id)[product_id]
            restored_token, restored_blocks = evaluate(restored, state)
            assert fingerprint(restored) == original
            assert restored_token == original_token and restored_blocks == before_blocks
            assert db.session.get(Record, record_id).include_in_demand is True
            return {"passed": True, "product_id": product_id, "record_id": record_id,
                    "import_id": import_id, "horizons": [7, 30, 90],
                    "synthetic_metadata_verified": True, "source_quantity_untouched": True,
                    "canonical_series_changed": True, "job_identity_changed": True,
                    "forecasts_changed": True, "rollback_series_exact": True,
                    "rollback_forecasts_exact": True, "commits": 0}
        finally:
            db.session.rollback()
            db.session.remove()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if os.name != "posix" or not output.is_relative_to(Path("/tmp")) or output.suffix != ".json":
        raise ValueError("Report must be JSON strictly beneath container /tmp")
    if output.exists():
        raise ValueError("Existing validation evidence will not be replaced")
    try:
        report = verify()
    except Exception as error:
        report = {"passed": False, "error_type": type(error).__name__}
        raise
    finally:
        output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"passed": True, "commits": 0}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"failed": True, "error_type": type(error).__name__}), flush=True)
        raise SystemExit(1) from None
