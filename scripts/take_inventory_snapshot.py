"""Ejecutor de cierres diarios persistidos; activación explícita y sin backfill.

Docker: python -m scripts.take_inventory_snapshot --database <DB_NAME>
Windows: python scripts/take_inventory_snapshot.py --database ferreteria_conejo_e2e
Requiere INVENTORY_SNAPSHOT_ACTIVATED_AT con fecha ISO 8601 y zona horaria.
Solo ejecuta el turno vigente (00:05–00:06 America/Caracas); no acepta fechas.
"""
import argparse
from datetime import datetime
import os
import sys
from urllib.parse import quote_plus

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

def run(now=None):
    from flask import current_app
    from app.services import inventory_snapshot_service as snapshots
    activation = current_app.config.get("INVENTORY_SNAPSHOT_ACTIVATED_AT", "")
    if not activation:
        return {"skipped": True, "reason": "disabled"}
    activated_at = datetime.fromisoformat(activation)
    if activated_at.tzinfo is None or activated_at.utcoffset() is None:
        raise ValueError("La activación del cierre debe incluir zona horaria.")
    local, scheduled, punctual = snapshots.capture_slot(now)
    if scheduled < activated_at or local < activated_at:
        return {"skipped": True, "reason": "before_activation"}
    if not punctual:
        return {"skipped": True, "reason": "outside_capture_window"}
    return snapshots.take_snapshot(now)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    portable = os.environ.get("CONEJO_CONTAINER") == "1"
    if portable:
        from docker_support.guard import validate, wait_database
        if args.database != validate():
            raise SystemExit("La base solicitada no coincide con el entorno portátil.")
        wait_database()
        from docker_support.bootstrap import verify_installation
        verify_installation()
    elif args.database not in ("ferreteria_conejo", "ferreteria_conejo_e2e"):
        raise SystemExit("Base no admitida fuera del entorno portátil.")
    from app import create_app
    from app.config import Config
    from app.extensions import db
    from sqlalchemy import text

    class SnapshotConfig(Config):
        SQLALCHEMY_DATABASE_URI = (
            f"mysql+pymysql://{Config.DB_USER}:{quote_plus(Config.DB_PASSWORD)}"
            f"@{Config.DB_HOST}:{Config.DB_PORT}/{args.database}?charset=utf8mb4"
        )

    app = create_app(SnapshotConfig)
    try:
        with app.app_context():
            if db.session.execute(text("SELECT DATABASE()")).scalar() != args.database:
                raise RuntimeError("La conexión no apunta a la base indicada.")
            print(run())
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Cierre diario fallido ({type(error).__name__}); sin detalles privados.")
        raise SystemExit(1)
