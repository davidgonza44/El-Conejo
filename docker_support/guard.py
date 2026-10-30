"""Fail closed before opening any database connection."""
import os
import re
import time

import pymysql


def validate():
    kind = os.environ.get("DOCKER_ENV_KIND")
    database = os.environ.get("DB_NAME", "")
    if kind not in ("disposable", "demo") or not re.fullmatch(r"conejo_" + kind + r"_[a-z0-9_]{8,32}", database):
        raise RuntimeError("Only an explicitly named disposable/demo container database is allowed.")
    if os.environ.get("DB_HOST") != "db" or os.environ.get("DB_PORT") != "3306":
        raise RuntimeError("Only the private Compose db:3306 service is allowed.")
    if os.environ.get("CONEJO_CONTAINER") != "1" or os.environ.get("PYTHON_DOTENV_DISABLED") != "1":
        raise RuntimeError("Container environment required; dotenv must be disabled.")
    if not re.fullmatch(r"[a-f0-9]{32}", os.environ.get("ENVIRONMENT_ID", "")):
        raise RuntimeError("Explicit environment identity required.")
    for name in ("DB_PASSWORD", "SECRET_KEY", "SECRETS_ENCRYPTION_KEY"):
        if len(os.environ.get(name, "")) < 32:
            raise RuntimeError(f"A generated {name} is required.")
    if os.environ.get("DB_USER", "root") == "root":
        raise RuntimeError("The application must not use database root.")
    if (os.environ.get("DEMO_MODE") == "true") != (kind == "demo"):
        raise RuntimeError("Demo data require a separate demo environment.")
    if kind == "demo":
        _demo_flags()
    return database


def _demo_flags():
    if os.environ.get("APP_ENV") != "demo" or os.environ.get("FLASK_ENV", "demo") == "production":
        raise RuntimeError("DEMO refuses production/unknown application environments.")
    if os.environ.get("APP_BASE_URL") != "http://localhost:5001":
        raise RuntimeError("DEMO must remain on localhost:5001.")
    if os.environ.get("DEMO_SEED_VERSION", "2") != "2":
        raise RuntimeError("Unsupported DEMO_SEED_VERSION; no automatic migration.")


def validate_demo():
    # Kind and mode checked first, even when called outside normal bootstrap.
    if os.environ.get("DEMO_MODE") != "true" or os.environ.get("DOCKER_ENV_KIND") != "demo":
        raise RuntimeError("Only an explicitly enabled DEMO environment is allowed.")
    return validate()


def validate_demo_url(url):
    """Validate even a stale/custom Flask configuration before its first connect."""
    database = validate_demo()
    if (url.host != "db" or url.port != 3306 or url.database != database
            or url.username != os.environ["DB_USER"] or url.drivername != "mysql+pymysql"):
        raise RuntimeError("DEMO engine target mismatch; connection refused.")


def wait_database():
    database = validate()
    deadline = time.monotonic() + 120
    while True:
        try:
            connection = pymysql.connect(host="db", port=3306, user=os.environ["DB_USER"],
                password=os.environ["DB_PASSWORD"], database=database, connect_timeout=3,
                read_timeout=5, write_timeout=5, charset="utf8mb4")
            with connection, connection.cursor() as cursor:
                cursor.execute("SELECT DATABASE(), VERSION()")
                actual, version = cursor.fetchone()
                if actual != database or not version.startswith("10.11.14-MariaDB"):
                    raise RuntimeError("Wrong database or engine version; no writes allowed.")
            return
        except pymysql.OperationalError as error:
            if error.args[0] in (1044, 1045, 1049) or time.monotonic() >= deadline:
                raise RuntimeError("Database unavailable or credentials invalid; no connection details logged.") from None
            time.sleep(2)
