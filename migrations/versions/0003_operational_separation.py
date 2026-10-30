"""Cash snapshots and minimal system-role separation on managed containers only."""
import os

from alembic import op
from sqlalchemy import text

from docker_support.guard import validate
from docker_support.bootstrap import identity
from scripts.migrate_operational_separation import apply_changes

revision = "0003_operational_separation"
down_revision = "0002_system_customer"
branch_labels = None
depends_on = None


def upgrade():
    database = validate()
    connection = op.get_bind()
    if connection.execute(text("SELECT DATABASE()")).scalar_one() != database:
        raise RuntimeError("Isolated migration target mismatch.")
    identity(connection)
    if os.environ["DOCKER_ENV_KIND"] == "demo":
        from docker_support.demo import preflight
        preflight(connection)  # Clean new install or a complete, matching existing seed only.
    apply_changes(connection)


def downgrade():
    raise RuntimeError("Cash history and role separation are not automatically reversible.")
