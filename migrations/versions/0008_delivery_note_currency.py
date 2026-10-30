"""Prepared delivery-note denomination; no defaults, backfill or amount changes.

Do not apply until the operator separately authorizes the schema migration.
Historical denomination assignments require their own explicit approval.
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_delivery_note_currency"
down_revision = "0007_catalog_currency"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("delivery_notes") as batch:
        batch.add_column(sa.Column("currency", sa.String(8), nullable=True))
        batch.create_check_constraint(
            "ck_delivery_notes_currency",
            "currency IS NULL OR HEX(currency) IN ('555344','564553')",
        )


def downgrade():
    raise RuntimeError("Recover documentary denominations from a verified backup; no automatic downgrade.")
