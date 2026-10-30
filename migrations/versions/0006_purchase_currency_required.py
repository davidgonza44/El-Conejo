"""Require an explicit USD/VES denomination without a default or conversion."""
from alembic import op
import sqlalchemy as sa

revision = "0006_purchase_currency_required"
down_revision = "0005_purchase_currency"
branch_labels = None
depends_on = None


def upgrade():
    from docker_support.guard import validate
    from docker_support.bootstrap import identity

    connection = op.get_bind()
    if connection.execute(sa.text("SELECT DATABASE()")).scalar_one() != validate():
        raise RuntimeError("Isolated migration target mismatch.")
    identity(connection)
    invalid = connection.execute(sa.text(
        "SELECT COUNT(*) FROM purchase_orders WHERE currency IS NULL "
        "OR BINARY currency NOT IN ('USD','VES')")).scalar_one()
    if invalid:
        raise RuntimeError("Unresolved purchase currencies; explicit regularization required before final migration.")
    # VARCHAR(3) silently trims 'VES ' even in strict MariaDB; CHECK must see it.
    op.alter_column("purchase_orders", "currency", existing_type=sa.String(3), type_=sa.String(8),
                    nullable=False, server_default=None)
    if "ck_po_currency" not in {c['name'] for c in sa.inspect(connection).get_check_constraints('purchase_orders')}:
        op.create_check_constraint("ck_po_currency", "purchase_orders", "HEX(currency) IN (HEX('USD'),HEX('VES'))")


def downgrade():
    raise RuntimeError("Required purchase denominations must not be relaxed automatically.")
