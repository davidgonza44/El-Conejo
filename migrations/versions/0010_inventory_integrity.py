"""Reglas del stock en la base y columnas de robustez del inventario.

- CHECK en products (stock y mínimo no negativos) y en stock_movements (tipo,
  cantidades y aritmética anterior/nuevo).
- stock_movements.idempotency_key: reintentos de entradas y salidas manuales.
- products.proposed_initial_stock: el stock inicial entra al aprobar el alta.
- inventory_adjustment_requests.reconciliation_line_id: ajustes de conciliación
  pasan por la misma aprobación que los manuales.

Antes de escribir, verifica que los datos existentes cumplan las reglas. Si no
cumplen, se detiene sin cambios: los datos inválidos se revisan a mano.
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_inventory_integrity"
down_revision = "0009_supplier_changes"
branch_labels = None
depends_on = None

MOVEMENT_ARITHMETIC = (
    "(movement_type = 'entrada' AND new_stock = previous_stock + quantity) OR "
    "(movement_type = 'salida' AND new_stock = previous_stock - quantity) OR "
    "(movement_type = 'ajuste' AND (new_stock = previous_stock + quantity OR new_stock = previous_stock - quantity))"
)

PRECHECKS = {
    "productos con stock o mínimo negativo":
        "SELECT COUNT(*) FROM products WHERE current_stock < 0 OR minimum_stock < 0",
    "movimientos con tipo desconocido":
        "SELECT COUNT(*) FROM stock_movements WHERE movement_type NOT IN ('entrada','salida','ajuste')",
    "movimientos con cantidad o stock inválidos":
        "SELECT COUNT(*) FROM stock_movements WHERE quantity <= 0 OR previous_stock < 0 OR new_stock < 0",
    "movimientos cuya aritmética no cuadra":
        "SELECT COUNT(*) FROM stock_movements WHERE NOT (" + MOVEMENT_ARITHMETIC + ")",
}


def precheck(connection):
    problems = {label: connection.execute(sa.text(sql)).scalar_one() for label, sql in PRECHECKS.items()}
    problems = {label: count for label, count in problems.items() if count}
    if problems:
        detail = "; ".join(f"{count} {label}" for label, count in problems.items())
        raise RuntimeError("Datos de inventario inválidos; revíselos antes de migrar: " + detail + ".")


def upgrade():
    precheck(op.get_bind())
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("proposed_initial_stock", sa.Integer, nullable=True))
        batch.create_check_constraint("ck_products_stock", "current_stock >= 0 AND minimum_stock >= 0")
        batch.create_check_constraint("ck_products_proposed_initial_stock",
                                      "proposed_initial_stock IS NULL OR proposed_initial_stock > 0")
    with op.batch_alter_table("stock_movements") as batch:
        batch.add_column(sa.Column("idempotency_key", sa.String(64), nullable=True))
        batch.create_unique_constraint("uq_stock_movements_idempotency_key", ["idempotency_key"])
        batch.create_check_constraint("ck_movement_type", "movement_type IN ('entrada','salida','ajuste')")
        batch.create_check_constraint("ck_movement_quantities",
                                      "quantity > 0 AND previous_stock >= 0 AND new_stock >= 0")
        batch.create_check_constraint("ck_movement_arithmetic", MOVEMENT_ARITHMETIC)
        batch.create_check_constraint("ck_movement_idempotency_manual",
                                      "idempotency_key IS NULL OR creation_origin = 'manual'")
    with op.batch_alter_table("inventory_adjustment_requests") as batch:
        batch.add_column(sa.Column("reconciliation_line_id", sa.Integer, nullable=True))
        batch.create_foreign_key("fk_adjustment_reconciliation_line", "reconciliation_lines",
                                 ["reconciliation_line_id"], ["id"])
        batch.create_index("ix_inventory_adjustment_requests_reconciliation_line_id", ["reconciliation_line_id"])


def downgrade():
    raise RuntimeError("Las reglas de integridad del inventario no se retiran automáticamente.")
