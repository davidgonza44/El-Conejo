"""Propuestas de modificación de proveedores separadas de los datos oficiales.

Solo crea la tabla supplier_changes; no toca proveedores ni órdenes existentes.
Aplicar cuando el operador autorice la migración del esquema.
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_supplier_changes"
down_revision = "0008_delivery_note_currency"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "supplier_changes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("supplier_id", sa.Integer, sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("proposed_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("changes", sa.JSON, nullable=False),
        sa.Column("previous_values", sa.JSON, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("reviewed_by_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime),
        sa.Column("rejection_reason", sa.String(1000)),
        sa.CheckConstraint("status IN ('pending','approved','rejected')", name="ck_supplier_change_status"),
    )
    op.create_index("ix_supplier_changes_supplier_status", "supplier_changes", ["supplier_id", "status"])


def downgrade():
    op.drop_index("ix_supplier_changes_supplier_status", table_name="supplier_changes")
    op.drop_table("supplier_changes")
