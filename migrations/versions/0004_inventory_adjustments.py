"""Manual adjustment requests; additive schema and minimal RBAC grants."""
import os

from alembic import op
import sqlalchemy as sa

revision = "0004_inventory_adjustments"
down_revision = "0003_operational_separation"
branch_labels = None
depends_on = None


def apply_changes(connection):
    # Frozen DDL: do not import mutable model metadata in historical migrations.
    metadata = sa.MetaData()
    for name in ("products", "users", "stock_movements"):
        sa.Table(name, metadata, sa.Column("id", sa.Integer, primary_key=True))
    table = sa.Table(
        "inventory_adjustment_requests", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("product_id", sa.Integer, sa.ForeignKey("products.id"), nullable=False, index=True),
        sa.Column("requester_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("requested_at", sa.DateTime, nullable=False),
        sa.Column("previous_stock", sa.Integer, nullable=False),
        sa.Column("previous_movement_id", sa.Integer, sa.ForeignKey("stock_movements.id")),
        sa.Column("new_stock", sa.Integer, nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("external_reference", sa.String(120)),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("reviewer_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime),
        sa.Column("review_comment", sa.String(255)),
        sa.Column("stock_movement_id", sa.Integer, sa.ForeignKey("stock_movements.id"), unique=True),
        sa.CheckConstraint("status IN ('pending','approved','rejected')", name="ck_adjustment_status"),
        sa.CheckConstraint("previous_stock >= 0 AND new_stock >= 0", name="ck_adjustment_stock"),
        sa.CheckConstraint("reviewer_user_id IS NULL OR reviewer_user_id <> requester_user_id", name="ck_adjustment_reviewer"),
        sa.CheckConstraint(
            "(status = 'pending' AND reviewer_user_id IS NULL AND reviewed_at IS NULL AND stock_movement_id IS NULL) OR "
            "(status = 'approved' AND reviewer_user_id IS NOT NULL AND reviewed_at IS NOT NULL AND stock_movement_id IS NOT NULL) OR "
            "(status = 'rejected' AND reviewer_user_id IS NOT NULL AND reviewed_at IS NOT NULL AND stock_movement_id IS NULL)",
            name="ck_adjustment_resolution"),
        mysql_engine="InnoDB",
    )
    table.create(connection, checkfirst=True)
    # Later migrations may add columns (0011: reconciliation_line_id); none of these may be missing.
    if not set(table.columns.keys()) <= {c["name"] for c in sa.inspect(connection).get_columns(table.name)}:
        raise RuntimeError("Unexpected adjustment request schema; manual review required.")
    grants = {"create": ("inventario", "Solicitar ajuste"), "approve": ("admin", "Aprobar o rechazar ajuste")}
    for action, (role, label) in grants.items():
        values = {"code": "inventory_adjustments:" + action, "role": role, "label": label}
        connection.execute(sa.text(
            "INSERT INTO permissions(code,module,label) SELECT :code,'inventory_adjustments',:label "
            "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code=:code)"), values)
        connection.execute(sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code=:code) "
            "AND role_id IN (SELECT id FROM roles WHERE is_system=1 AND slug<>:role)"), values)
        connection.execute(sa.text(
            "INSERT INTO role_permissions(role_id,permission_id) SELECT r.id,p.id FROM roles r "
            "JOIN permissions p ON p.code=:code WHERE r.slug=:role AND r.is_system=1 "
            "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id)"), values)


def upgrade():
    from docker_support.guard import validate
    from docker_support.bootstrap import identity
    database = validate()
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT DATABASE()")).scalar_one() != database:
        raise RuntimeError("Isolated migration target mismatch.")
    identity(connection)
    if os.environ["DOCKER_ENV_KIND"] == "demo":
        from docker_support.demo import preflight
        preflight(connection)
    apply_changes(connection)


def downgrade():
    raise RuntimeError("Adjustment audit history must not be removed automatically.")
