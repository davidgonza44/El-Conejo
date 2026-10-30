"""Notas de entrega: cliente del directorio, motivo de cancelación, clave por envío y numeración.

- delivery_notes.customer_id: vínculo opcional con customers; la nota sigue guardando su copia del nombre.
- delivery_notes.cancellation_reason: motivo obligatorio al cancelar (las notas ya canceladas quedan sin motivo).
- delivery_notes.idempotency_key: un reintento del mismo envío devuelve la nota ya emitida.
- document_sequences: último número usado; continúa desde la última nota existente.

No modifica notas, importes ni existencias. Aplicar cuando el operador autorice la migración del esquema.
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_delivery_note_robustness"
down_revision = "0009_supplier_changes"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("delivery_notes") as batch:
        batch.add_column(sa.Column("customer_id", sa.Integer, nullable=True))
        batch.add_column(sa.Column("cancellation_reason", sa.String(500), nullable=True))
        batch.add_column(sa.Column("idempotency_key", sa.String(64), nullable=True))
        batch.create_foreign_key("fk_delivery_notes_customer_id", "customers", ["customer_id"], ["id"])
        batch.create_index("ix_delivery_notes_customer_id", ["customer_id"])
        batch.create_unique_constraint("uq_delivery_notes_idempotency_key", ["idempotency_key"])
    op.create_table(
        "document_sequences",
        sa.Column("name", sa.String(40), primary_key=True),
        sa.Column("last_value", sa.Integer, nullable=False),
    )
    # Los números existentes son NE-{id}: el siguiente continúa después del mayor id.
    op.execute("INSERT INTO document_sequences (name, last_value) "
               "SELECT 'delivery_note', COALESCE(MAX(id), 0) FROM delivery_notes")


def downgrade():
    op.drop_table("document_sequences")
    with op.batch_alter_table("delivery_notes") as batch:
        batch.drop_constraint("uq_delivery_notes_idempotency_key", type_="unique")
        batch.drop_index("ix_delivery_notes_customer_id")
        batch.drop_constraint("fk_delivery_notes_customer_id", type_="foreignkey")
        batch.drop_column("idempotency_key")
        batch.drop_column("cancellation_reason")
        batch.drop_column("customer_id")
