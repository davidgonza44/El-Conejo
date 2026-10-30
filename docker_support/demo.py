"""Versioned DEMO orchestration. Business services own all stock and documents."""
import json
import os

from sqlalchemy import inspect, text

from docker_support.demo_data import ANCHOR, DEMO_SEED_VERSION, RANDOM_SEED, manifest_hash
from docker_support.guard import validate_demo, validate_demo_url


def preflight(connection):
    """Read-only check, also called BEFORE migrations/RBAC/admin writes."""
    validate_demo()
    validate_demo_url(connection.engine.url)
    if connection.execute(text("SELECT DATABASE()")).scalar_one() != os.environ["DB_NAME"]:
        raise RuntimeError("DEMO database mismatch; no writes allowed.")
    tables = set(inspect(connection).get_table_names())
    if "container_installation" not in tables:
        raise RuntimeError("DEMO requires the managed container installation registry.")
    from docker_support.bootstrap import identity
    identity(connection)
    if "demo_seed" in tables:
        row = connection.execute(text("SELECT * FROM demo_seed WHERE id=1")).mappings().one_or_none()
        if not row or row["version"] != DEMO_SEED_VERSION or row["manifest"] != manifest_hash():
            raise RuntimeError("DEMO seed version/content mismatch. Use the documented controlled reset.")
        if row["status"] != "ready":
            raise RuntimeError("DEMO seed incomplete. Automatic repair is forbidden; use controlled reset.")
        return True
    for table in ("products", "sales", "stock_movements", "suppliers", "historical_imports", "purchase_orders"):
        if table in tables and connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one():
            raise RuntimeError("DEMO contains an unversioned dataset. Use controlled reset; no automatic migration.")
    return False


def require_ready(connection):
    if not preflight(connection):
        raise RuntimeError("DEMO seed has not completed.")


def seed(app, admin):
    validate_demo()
    if app.config["APP_ENV"] != "demo":
        raise RuntimeError("DEMO refuses a production application configuration.")
    from app.extensions import db
    from docker_support.demo_operations import populate

    validate_demo_url(db.engine.url)
    with db.engine.connect() as connection:
        if preflight(connection):
            print(f"DEMO seed v{DEMO_SEED_VERSION}: already ready, no data changes.")
            return
    db.session.execute(text("CREATE TABLE demo_seed (id INT PRIMARY KEY, version INT NOT NULL, "
        "random_seed INT NOT NULL, anchor DATE NOT NULL, manifest VARCHAR(64) NOT NULL, "
        "status VARCHAR(16) NOT NULL, counts_json TEXT NULL) ENGINE=InnoDB"))
    db.session.execute(text("INSERT INTO demo_seed VALUES(1,:v,:s,:a,:m,'building',NULL)"),
        {"v": DEMO_SEED_VERSION, "s": RANDOM_SEED, "a": ANCHOR, "m": manifest_hash()})
    db.session.commit()
    # Services own commits. Failure leaves 'building' and blocks retries/startup.
    populate(app, admin)
    from docker_support.demo_invariants import verify_dataset
    counts = verify_dataset()
    db.session.execute(text("UPDATE demo_seed SET status='ready', counts_json=:c WHERE id=1"),
                       {"c": json.dumps(counts, sort_keys=True)})
    db.session.execute(text("UPDATE container_installation SET demo_ready=1 WHERE id=1"))
    db.session.commit()
    print(f"DEMO seed v{DEMO_SEED_VERSION}: verified synthetic dataset. " + json.dumps(counts))
