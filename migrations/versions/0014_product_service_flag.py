"""Clasificación persistente de servicios en products.is_service.

Compras, reposición y pronóstico ya excluyen servicios, pero products no tenía la
columna y esas reglas nunca se activaban. Todos los productos existentes quedan como
no servicio: la clasificación se asigna de forma explícita y aprobada, nunca a partir
del nombre. Se detiene si un producto con existencias quedara marcado como servicio.
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_product_service_flag"
down_revision = "0013_schema_alignment"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("products")}
    if "is_service" not in columns:
        with op.batch_alter_table("products") as batch:
            batch.add_column(sa.Column("is_service", sa.Boolean, nullable=False, server_default=sa.false()))
    stocked = op.get_bind().execute(sa.text(
        "SELECT COUNT(*) FROM products WHERE is_service = 1 AND current_stock > 0")).scalar_one()
    if stocked:
        raise RuntimeError(f"{stocked} servicios con existencias; revise antes de migrar.")


def downgrade():
    raise RuntimeError("La clasificación de servicios se conserva; no se elimina automáticamente.")
