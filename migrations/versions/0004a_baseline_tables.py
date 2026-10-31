"""Línea base que faltaba en la cadena: tablas y columnas que ninguna migración creaba.

Diez tablas de los modelos (cotizaciones, solicitudes de reposición, cierres, propuestas,
históricos 2026) y varias columnas de aprobación y cierre se aplicaban con scripts
manuales fuera de Alembic. Por eso una instalación nueva fallaba en 0005, que ya
consulta supplier_quotes y purchase_orders.supplier_quote_id.

DDL congelado en migrations/baseline_0004a.json (no importa modelos mutables).
Todas las sentencias usan IF NOT EXISTS: en una base que ya las tiene no cambian nada.
Las bases desplegadas en 0007 o posterior nunca ejecutan esta revisión.
"""
import json
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0004a_baseline_tables"
down_revision = "0004_inventory_adjustments"
branch_labels = None
depends_on = None


def upgrade():
    statements = json.loads((Path(__file__).parent.parent / "baseline_0004a.json").read_text(encoding="utf-8"))
    for statement in statements:
        op.get_bind().execute(text(statement))


def downgrade():
    raise RuntimeError("La línea base no se revierte automáticamente.")
