"""Baseline inicial de product_state_histories (todo-o-nada, un solo timestamp).

Orden seguro de activación (requisito de despliegue):
1. crear product_state_histories (scripts/migrate_product_state_history.py);
2. ejecutar y verificar este baseline para todos los productos existentes;
3. solo después considerar habilitada la cobertura histórica.

Este script captura UNA vez coverage_started_at = datetime.utcnow() y usa ese
mismo timestamp para todas las filas baseline de la ejecución. Reporta:
    Coverage starts at: <timestamp UTC>
Ese instante es el límite oficial desde el cual el historial es confiable.

Sin --apply es --dry-run: valida y muestra cuántas filas crearía, sin escribir.
Con --apply: valida primero, inserta todas en UNA transacción (un commit);
cualquier error -> rollback completo.

Aborta sin escribir si detecta ambigüedad (no reconstruye el pasado):
- producto con change pero sin baseline;
- más de un baseline por producto;
- baseline inconsistente (previous != new);
- baseline que no es el primer evento.

Uso:
    python scripts/baseline_product_state_history.py --database ferreteria_conejo_e2e
    python scripts/baseline_product_state_history.py --database ferreteria_conejo_e2e --apply
"""
import argparse
import os
import sys
from datetime import datetime
from urllib.parse import quote_plus

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")

from sqlalchemy import text

from app import create_app
from app.config import Config
from app.extensions import db

DATABASES = ("ferreteria_conejo", "ferreteria_conejo_e2e", "ferreteria_conejo_tesis")


def _uri(database):
    return (f"mysql+pymysql://{Config.DB_USER}:{quote_plus(Config.DB_PASSWORD)}"
            f"@{Config.DB_HOST}:{Config.DB_PORT}/{database}?charset=utf8mb4")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, choices=DATABASES)
    parser.add_argument("--dry-run", action="store_true", help="solo valida y reporta, sin escribir (defecto)")
    parser.add_argument("--apply", action="store_true", help="inserta el baseline (sin esto es dry-run)")
    args = parser.parse_args()
    if args.dry_run and args.apply:
        raise SystemExit("--dry-run y --apply son excluyentes.")

    coverage_started_at = datetime.utcnow()
    print(f"Coverage starts at: {coverage_started_at.isoformat()} UTC")

    uri = _uri(args.database)

    class _Conf(Config):
        SQLALCHEMY_DATABASE_URI = uri

    app = create_app(_Conf)
    with app.app_context():
        if db.session.execute(text("SELECT DATABASE()")).scalar() != args.database:
            raise SystemExit("REFUSED: la conexión no apunta a la base indicada.")
        from app.services import product_state_history_service as history

        anomalies = history.validate_invariants()
        if any(anomalies.values()):
            raise SystemExit(f"ABORTADO: historial ambiguo, sin escrituras: {anomalies}.")
        planned = history.plan_baseline(coverage_started_at)
        if not args.apply:
            print(f"[DRY-RUN] {args.database}: se crearían {len(planned)} filas baseline "
                  f"con changed_at={coverage_started_at.isoformat()} UTC. Sin escrituras.")
            return
        result = history.apply_baseline(coverage_started_at)
        print(f"[OK] {args.database}: baseline aplicado. inserted={result['inserted']} "
              f"coverage_started_at={result['coverage_started_at']} UTC. Una transacción, un commit.")


if __name__ == "__main__":
    main()
