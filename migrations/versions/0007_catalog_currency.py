"""Add independent, nullable catalog currencies without defaults or data writes."""
from alembic import op
import sqlalchemy as sa

revision = "0007_catalog_currency"
down_revision = "0006_purchase_currency_required"
branch_labels = None
depends_on = None

FIELDS = {
    "products": ("sale_price_currency", "purchase_price_currency"),
    "product_suppliers": ("purchase_price_currency",),
}


def upgrade():
    for table, columns in FIELDS.items():
        # SQLite batch recreation and native MySQL/MariaDB ALTER are supported.
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.add_column(sa.Column(column, sa.String(8), nullable=True))
                batch.create_check_constraint(
                    "ck_" + table + "_" + column,
                    column + " IS NULL OR HEX(" + column + ") IN ('555344','564553')")


def downgrade():
    raise RuntimeError("Catalog denomination recovery requires a verified backup; no automatic downgrade.")
