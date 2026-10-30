"""Preserve migrate_customers.py's mandatory generic customer on new installs."""
from alembic import op
from sqlalchemy import text

revision = "0002_system_customer"
down_revision = "0001_current_schema"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().execute(text(
        "INSERT INTO customers (name, is_active, is_walk_in, created_at, updated_at) "
        "SELECT :name, 1, 1, UTC_TIMESTAMP(), UTC_TIMESTAMP() "
        "WHERE NOT EXISTS (SELECT 1 FROM customers WHERE is_walk_in=1)"
    ), {"name": "Público en general"})


def downgrade():
    raise RuntimeError("Automatic destructive downgrades are not supported.")
