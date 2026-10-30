"""Persist quote denominations; unknown history requires explicit operator decisions."""
from alembic import op
import sqlalchemy as sa

revision = "0005_purchase_currency"
down_revision = "0004_inventory_adjustments"
branch_labels = None
depends_on = None


def upgrade():
    from docker_support.guard import validate
    from docker_support.bootstrap import identity

    connection = op.get_bind()
    if connection.execute(sa.text("SELECT DATABASE()")).scalar_one() != validate():
        raise RuntimeError("Isolated migration target mismatch.")
    identity(connection)
    inspector = sa.inspect(connection)
    if "currency" not in {c["name"] for c in inspector.get_columns("purchase_orders")}:
        op.add_column("purchase_orders", sa.Column("currency", sa.String(8), nullable=True))
    connection.execute(sa.text(
        "UPDATE purchase_orders po JOIN supplier_quotes sq ON sq.id=po.supplier_quote_id "
        "SET po.currency=sq.currency WHERE po.currency IS NULL "
        "AND BINARY sq.currency IN ('USD','VES')"))
    if not sa.inspect(connection).has_table("purchase_currency_assignments"):
        op.create_table(
            "purchase_currency_assignments",
            sa.Column("purchase_order_id", sa.Integer, sa.ForeignKey("purchase_orders.id"), primary_key=True),
            sa.Column("order_number", sa.String(20), nullable=False),
            sa.Column("old_currency", sa.String(3), nullable=True),
            sa.Column("assigned_currency", sa.String(3), nullable=False),
            sa.Column("evidence_type", sa.String(64), nullable=False),
            sa.Column("source_csv_sha256", sa.String(64), nullable=False),
            sa.Column("approved_manifest_sha256", sa.String(64), nullable=False),
            sa.Column("approved_by", sa.String(150), nullable=False),
            sa.Column("approval_date", sa.Date, nullable=False),
            sa.Column("executed_at", sa.DateTime, nullable=False),
            sa.CheckConstraint("assigned_currency IN ('USD','VES')", name="ck_assignment_currency"),
            sa.CheckConstraint("evidence_type = 'asignacion_administrativa_sintetica'", name="ck_assignment_evidence"),
            mysql_engine="InnoDB",
        )


def downgrade():
    raise RuntimeError("Currency decisions and audit history must not be removed automatically.")
