"""Explicit DEMO 5001 upgrade; never connects to the operational/thesis database.

Run inside the existing, stopped DEMO Compose environment:
    python -B scripts/migrate_operational_separation.py --apply
Without --apply this only prints the plan. DDL auto-commits on MariaDB; the
additive steps are restartable and the RBAC delta is transactional. No cash
history is inferred, no stock/account/seed rows are changed, and no dotenv is read.
"""
import argparse
import os
import sys
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text

REVISION = "0003_operational_separation"
GRANTS = {"inventario": {"purchases:read", "purchases:write", "purchases:receive"},
          "admin": {"purchases:read", "purchases:approve"}}
REVOKES = {"admin": {"sales:create", "purchases:write", "purchases:receive"},
           "inventario": {"sales:create", "purchases:approve"},
           "soporte_ti": {"sales:create", "purchases:write", "purchases:receive", "purchases:approve"},
           "vendedor": {"purchases:write", "purchases:receive", "purchases:approve"}}


def apply_changes(connection):
    """Shared additive DDL/RBAC delta; callers must validate the isolated target first."""
    inspector = inspect(connection)
    if not all(inspector.has_table(t) for t in ("sales", "roles", "permissions", "role_permissions")):
        raise RuntimeError("Existing sales and RBAC schema required.")
    columns = {column["name"]: column for column in inspector.get_columns("sales")}
    for name in ("cash_received", "change_due"):
        column = columns.get(name)
        if column is not None:
            kind = column["type"]
            if not column["nullable"] or getattr(kind, "precision", None) != 12 or getattr(kind, "scale", None) != 2:
                raise RuntimeError("Existing cash schema incompatible; operator review required.")
        else:
            connection.execute(text(f"ALTER TABLE sales ADD COLUMN {name} DECIMAL(12,2) NULL"))
    # Generic SQL makes the same minimal delta testable on disposable SQLite.
    from app.utils.permissions import permission_label

    for code in sorted(set().union(*GRANTS.values())):
        connection.execute(text("INSERT INTO permissions (code,module,label) SELECT :code,:module,:label "
                                "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code=:code)"),
                           {"code": code, "module": code.split(":")[0], "label": permission_label(code)})
    for slug, codes in REVOKES.items():
        for code in sorted(codes):
            connection.execute(text("DELETE FROM role_permissions WHERE role_id IN "
                                    "(SELECT id FROM roles WHERE slug=:slug AND is_system=1) "
                                    "AND permission_id IN (SELECT id FROM permissions WHERE code=:code)"),
                               {"slug": slug, "code": code})
    for slug, codes in GRANTS.items():
        for code in sorted(codes):
            connection.execute(text("INSERT INTO role_permissions (role_id,permission_id) "
                                    "SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code=:code "
                                    "WHERE r.slug=:slug AND r.is_system=1 AND NOT EXISTS "
                                    "(SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id)"),
                               {"slug": slug, "code": code})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("PLAN ONLY: nullable cash snapshots; system-role operational RBAC delta; no connection.")
        return
    from docker_support.guard import validate_demo

    validate_demo()  # Before configuration import or first connection.
    from alembic import command
    from alembic.config import Config
    from docker_support.bootstrap import engine, identity
    from docker_support.demo import require_ready

    target = engine()
    try:
        with target.connect() as connection:
            require_ready(connection)
            if not identity(connection).ready:
                raise RuntimeError("DEMO installation is not ready.")
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if version not in ("0002_system_customer", REVISION):
                raise RuntimeError("Unexpected DEMO schema revision; no upgrade allowed.")
        command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), REVISION)
        print("PASS: DEMO cash schema and operational RBAC upgraded; no stock, seed or account changes.")
    finally:
        target.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Upgrade refused/failed; inspect guarded preconditions. No credentials or database details logged.")
        raise SystemExit(1)
