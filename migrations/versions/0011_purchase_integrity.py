"""Histórico de proveedor y correcciones autorizadas de recepción. No reconstruye históricos."""
from alembic import op
import sqlalchemy as sa

revision = "0011_purchase_integrity"
down_revision = "0010_inventory_integrity"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("purchase_orders", sa.Column("supplier_snapshot", sa.JSON(), nullable=True))
    op.add_column("supplier_changes", sa.Column("reason", sa.String(1000), nullable=True))
    op.create_table("purchase_reception_corrections",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("reception_id", sa.Integer, sa.ForeignKey("purchase_receptions.id"), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("decided_by_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("decided_at", sa.DateTime), sa.Column("decision_note", sa.String(1000)),
        sa.CheckConstraint("status IN ('pending','approved','rejected')", name="ck_receipt_correction_status"))
    op.create_index("ix_purchase_reception_corrections_reception_id", "purchase_reception_corrections", ["reception_id"])
    op.create_index("ix_purchase_reception_corrections_status", "purchase_reception_corrections", ["status"])
    op.create_table("purchase_reception_correction_lines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("correction_id", sa.Integer, sa.ForeignKey("purchase_reception_corrections.id"), nullable=False),
        sa.Column("purchase_order_line_id", sa.Integer, sa.ForeignKey("purchase_order_lines.id"), nullable=False),
        sa.Column("product_id", sa.Integer, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("previous_quantity", sa.Integer, nullable=False), sa.Column("target_quantity", sa.Integer, nullable=False),
        sa.Column("expected_stock", sa.Integer, nullable=False),
        sa.Column("previous_movement_id", sa.Integer, sa.ForeignKey("stock_movements.id")),
        sa.Column("stock_movement_id", sa.Integer, sa.ForeignKey("stock_movements.id"), unique=True),
        sa.UniqueConstraint("correction_id", "purchase_order_line_id", name="uq_receipt_correction_line"),
        sa.CheckConstraint("previous_quantity >= 0 AND target_quantity >= 0 AND previous_quantity <> target_quantity", name="ck_receipt_correction_quantities"))
    op.create_index("ix_purchase_reception_correction_lines_correction_id", "purchase_reception_correction_lines", ["correction_id"])


def downgrade():
    raise RuntimeError("Las correcciones y el histórico de proveedor se conservan; no se borran automáticamente.")
