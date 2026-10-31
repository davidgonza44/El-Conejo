"""Representa en Alembic las columnas que la base operativa de 5011 ya tiene.

La base de 5011 trae columnas de conciliación creadas fuera de esta cadena, con datos
históricos (estado, perfil y contexto de cortes). Esta migración:

- las crea con la misma definición cuando faltan (instalación nueva);
- las adopta sin tocar filas cuando ya existen;
- se detiene si una columna existe con otro tipo o nulabilidad.

No elimina columnas, no reescribe valores y no altera tablas sin modelo.
`delivery_notes.currency` la crea 0008; aquí solo se comprueba.
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_schema_alignment"
down_revision = "0012_purchase_integrity"
branch_labels = None
depends_on = None

RUN_COLUMNS = (
    ("confirmed_by_user_id", sa.Integer, "fk_reconciliation_runs_confirmed_by"),
    ("confirmed_at", sa.DateTime, None),
    ("discarded_by_user_id", sa.Integer, "fk_reconciliation_runs_discarded_by"),
    ("discarded_at", sa.DateTime, None),
    ("delivery_method", lambda: sa.String(20), None),
    ("coverage_status", lambda: sa.String(12), None),
    ("comparability_status", lambda: sa.String(20), None),
    ("has_differences", sa.Boolean, None),
    ("policy_version", lambda: sa.String(32), None),
    ("basis_digest", lambda: sa.String(64), None),
    ("policy_json", sa.JSON, None),
    ("live_basis_changed", sa.Boolean, None),
)
CUT_COLUMNS = (
    ("execution_state", lambda: sa.String(12)),
    ("schedule_version", lambda: sa.String(32)),
    ("profile_id", lambda: sa.String(50)),
    ("registered_at", sa.DateTime),
    ("detected_at", sa.DateTime),
    ("reason_code", lambda: sa.String(40)),
    ("reason_confidence", lambda: sa.String(12)),
    ("registration_kind", lambda: sa.String(16)),
    ("inventory_realm", lambda: sa.String(16)),
    ("business_entity_id", lambda: sa.String(64)),
    ("source_installation_id", lambda: sa.String(64)),
    ("warehouse_scope_id", lambda: sa.String(64)),
    ("snapshot_context_json", sa.JSON),
)
# Tipos que MariaDB devuelve al reflejar cada tipo declarado (JSON se guarda como LONGTEXT).
COMPATIBLE = {"Integer": ("INTEGER",), "DateTime": ("DATETIME",), "Boolean": ("TINYINT", "BOOLEAN"),
              "JSON": ("JSON", "LONGTEXT"), "String": ("VARCHAR",)}


def _check(table, name, factory, column):
    declared = factory()
    kind = type(declared).__name__
    actual = type(column["type"]).__name__.upper()
    if not column["nullable"] or actual not in COMPATIBLE[kind]:
        raise RuntimeError(f"{table}.{name} existe con otra definición ({actual}); revise antes de migrar.")
    if kind == "String" and getattr(column["type"], "length", None) != declared.length:
        raise RuntimeError(f"{table}.{name} existe con otra longitud; revise antes de migrar.")


def _align(table, columns):
    existing = {column["name"]: column for column in sa.inspect(op.get_bind()).get_columns(table)}
    missing = []
    for name, factory, *fk in columns:
        if name in existing:
            _check(table, name, factory, existing[name])
        else:
            missing.append((name, factory, fk[0] if fk else None))
    if not missing:
        return
    with op.batch_alter_table(table) as batch:
        for name, factory, fk in missing:
            batch.add_column(sa.Column(name, factory(), nullable=True))
            if fk:
                batch.create_foreign_key(fk, "users", [name], ["id"])


def upgrade():
    _align("reconciliation_runs", RUN_COLUMNS)
    _align("reconciliation_cuts", CUT_COLUMNS)
    currency = {c["name"]: c for c in sa.inspect(op.get_bind()).get_columns("delivery_notes")}.get("currency")
    if currency is None or not currency["nullable"]:
        raise RuntimeError("delivery_notes.currency debe existir y admitir NULL (migración 0008).")


def downgrade():
    raise RuntimeError("Las columnas históricas se conservan; no se eliminan automáticamente.")
