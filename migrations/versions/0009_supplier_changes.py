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


EXPECTED_COLUMNS = {"id", "supplier_id", "proposed_by_user_id", "changes", "previous_values", "status",
                    "created_at", "reviewed_by_user_id", "reviewed_at", "rejection_reason"}


def upgrade():
    # Bases desplegadas recibieron esta tabla con scripts/migrate_supplier_changes.py, fuera de Alembic.
    # Si ya existe con la forma esperada, se adopta sin tocar sus filas; si difiere, se detiene.
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("supplier_changes"):
        actual = {column["name"] for column in inspector.get_columns("supplier_changes")}
        if not EXPECTED_COLUMNS <= actual:
            raise RuntimeError("supplier_changes existe con otra estructura; revise antes de migrar: faltan "
                               + ", ".join(sorted(EXPECTED_COLUMNS - actual)))
        if "ix_supplier_changes_supplier_status" not in {index["name"] for index in inspector.get_indexes("supplier_changes")}:
            op.create_index("ix_supplier_changes_supplier_status", "supplier_changes", ["supplier_id", "status"])
        return
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
