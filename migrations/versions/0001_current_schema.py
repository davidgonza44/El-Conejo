"""Frozen MariaDB schema, including immutable historical-record triggers."""
import json
from pathlib import Path
from alembic import op
from sqlalchemy import text

revision = "0001_current_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    statements = json.loads((Path(__file__).parent.parent / "schema_v1.json").read_text(encoding="utf-8"))
    for statement in statements:
        op.get_bind().execute(text(statement))


def downgrade():
    raise RuntimeError("Automatic destructive downgrades are not supported.")
