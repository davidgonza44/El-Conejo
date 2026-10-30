"""Opt-in, restartable MariaDB migration; no application imports, seed or backfill.

Missing whole objects are created. Existing incompatible or incomplete objects
fail closed for operator review; restrictions are never silently repaired.
DDL commits independently. ORM defaults deliberately remain ORM-only.
"""
import argparse
import os
import re

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL


CUT_COLUMNS = {
    "id": ("int", False, "auto_increment"), "source": ("varchar(50)", False, ""),
    "scheduled_for": ("datetime", False, ""), "status": ("varchar(20)", False, ""),
    "snapshot_status": ("varchar(20)", False, ""), "local_snapshot_at": ("datetime", True, ""),
    "source_received_at": ("datetime", True, ""), "run_id": ("int", True, ""),
    "last_attempt_at": ("datetime", True, ""), "attempt_count": ("int", False, ""),
    "failure_reason": ("varchar(255)", True, ""), "last_source_hash": ("varchar(64)", True, ""),
    "archived_at": ("datetime", True, ""),
}
STOCK_COLUMNS = {
    "cut_id": ("int", False, ""), "product_id": ("int", False, ""),
    "product_code_snapshot": ("varchar(50)", False, ""), "quantity": ("int", False, ""),
}
CHECKS = {
    "ck_reconciliation_cut_status": "status IN ('waiting_source','completed','failed')",
    "ck_reconciliation_snapshot_status": "snapshot_status IN ('pending','captured','unavailable')",
}
CREATE_CUTS = """CREATE TABLE reconciliation_cuts (
    id INTEGER NOT NULL AUTO_INCREMENT PRIMARY KEY,
    source VARCHAR(50) NOT NULL, scheduled_for DATETIME NOT NULL,
    status VARCHAR(20) NOT NULL, snapshot_status VARCHAR(20) NOT NULL,
    local_snapshot_at DATETIME NULL, source_received_at DATETIME NULL,
    run_id INTEGER NULL, last_attempt_at DATETIME NULL, attempt_count INTEGER NOT NULL,
    failure_reason VARCHAR(255) NULL, last_source_hash VARCHAR(64) NULL, archived_at DATETIME NULL,
    CONSTRAINT uq_reconciliation_cut_source_time UNIQUE (source, scheduled_for),
    CONSTRAINT uq_reconciliation_cut_run UNIQUE (run_id),
    FOREIGN KEY (run_id) REFERENCES reconciliation_runs(id),
    CONSTRAINT ck_reconciliation_cut_status CHECK (status IN ('waiting_source','completed','failed')),
    CONSTRAINT ck_reconciliation_snapshot_status CHECK (snapshot_status IN ('pending','captured','unavailable'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
CREATE_STOCK = """CREATE TABLE reconciliation_cut_stock (
    cut_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
    product_code_snapshot VARCHAR(50) NOT NULL, quantity INTEGER NOT NULL,
    PRIMARY KEY (cut_id, product_id),
    FOREIGN KEY (cut_id) REFERENCES reconciliation_cuts(id),
    INDEX ix_reconciliation_cut_stock_product_code_snapshot (product_code_snapshot)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""


def validate_target(database: str, authorize_thesis: bool = False) -> None:
    if database == "ferreteria_conejo_tesis":
        if not authorize_thesis:
            raise RuntimeError("Thesis database requires separate express authorization.")
        return
    if not re.fullmatch(r"reconciliation_test_[a-f0-9]{12}", database):
        raise RuntimeError("Only a newly named disposable reconciliation database is allowed.")


def engine_for(database):
    # Explicit process environment only; no import can load app config or dotenv.
    return create_engine(URL.create(
        "mysql+pymysql", username=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", ""), host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", "3306")), database=database,
        query={"charset": "utf8mb4"}))


def _require(condition, detail):
    if not condition:
        raise RuntimeError(f"Incompatible schema: {detail}; operator review required. No automatic repair.")


def _columns(connection, table):
    return {row["COLUMN_NAME"]: row for row in connection.execute(text(
        "SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,EXTRA,COLUMN_COMMENT,"
        "GENERATION_EXPRESSION,CHARACTER_SET_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table"), {"table": table}).mappings()}


def _column(actual, expected, label, allow_not_null=False):
    _require(actual is not None, label + " missing")
    kind, nullable, extra = expected
    # Integer display width is not storage width; unsigned/zerofill remain significant.
    actual_type = re.sub(r"^int\(\d+\)$", "int", actual["COLUMN_TYPE"].lower())
    _require(actual_type == kind, label + " type/length/signedness")
    _require(allow_not_null or (actual["IS_NULLABLE"] == "YES") == nullable, label + " nullable")
    _require(actual["COLUMN_DEFAULT"] in (None, "NULL"), label + " server default")
    _require(actual["EXTRA"].lower() == extra, label + " extra attributes")
    _require(not actual["COLUMN_COMMENT"] and not actual["GENERATION_EXPRESSION"], label + " generated/comment attributes")
    if kind.startswith("varchar"):
        _require(actual["CHARACTER_SET_NAME"] == "utf8mb4", label + " character set")


def _foreign_keys(inspector, table, expected, only_column=None):
    actual = inspector.get_foreign_keys(table)
    if only_column:
        actual = [fk for fk in actual if only_column in fk["constrained_columns"]]
    signatures = []
    for fk in actual:
        options = fk.get("options", {})
        _require(all(options.get(action, "RESTRICT").upper() in ("RESTRICT", "NO ACTION")
                     for action in ("ondelete", "onupdate")), table + " FK actions")
        _require(fk.get("referred_schema") in (None, inspector.bind.engine.url.database), table + " FK schema")
        signatures.append((tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"])))
    _require(sorted(signatures) == sorted(expected), table + " foreign keys")


def _check_tokens(expression):
    # Preserve literals exactly. Accept only the narrow IN grammar emitted by the
    # approved DDL; equivalent but differently expressed checks require review.
    pattern = r"\s+|`[^`]+`|'(?:''|[^'])*'|[A-Za-z_][A-Za-z_0-9]*|[(),]"
    parts = re.findall(pattern, expression)
    _require("".join(parts) == expression, "unsupported CHECK expression")
    tokens = [part if part.startswith("'") else part.strip("`").lower()
              for part in parts if not part.isspace()]
    while len(tokens) > 2 and tokens[0] == "(" and tokens[-1] == ")":
        tokens = tokens[1:-1]
    return tokens


def _checks(connection, table, expected):
    actual = {row[0]: row[1] for row in connection.execute(text(
        "SELECT CONSTRAINT_NAME,CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=:table"), {"table": table})}
    _require(set(actual) == set(expected), table + " CHECK constraints")
    for name, expression in expected.items():
        _require(_check_tokens(actual[name]) == _check_tokens(expression), table + "." + name)


def _indexes(connection, table, expected):
    rows = connection.execute(text(
        "SELECT INDEX_NAME,NON_UNIQUE,SEQ_IN_INDEX,COLUMN_NAME,SUB_PART,INDEX_TYPE,COLLATION "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table "
        "ORDER BY INDEX_NAME,SEQ_IN_INDEX"), {"table": table}).mappings()
    actual = {}
    for row in rows:
        _require(row["SUB_PART"] is None and row["INDEX_TYPE"] == "BTREE" and row["COLLATION"] == "A",
                 table + " index prefix/type/order")
        key = (row["INDEX_NAME"] == "PRIMARY", not bool(row["NON_UNIQUE"]), row["INDEX_NAME"])
        actual.setdefault(key, []).append(row["COLUMN_NAME"])
    # Names of secondary indexes are not semantically significant.
    signatures = sorted((pk, unique, tuple(columns)) for (pk, unique, _), columns in actual.items())
    _require(signatures == sorted(expected), table + " PK/UNIQUE/index contract")


def _table_storage(connection, table):
    metadata = connection.execute(text(
        "SELECT ENGINE,TABLE_TYPE,TABLE_COLLATION FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table"), {"table": table}).one_or_none()
    _require(metadata is not None and metadata[0] == "InnoDB" and metadata[1] == "BASE TABLE"
             and metadata[2].startswith("utf8mb4_"), table + " InnoDB/base table/charset")


def _required_indexes(connection, table, *, unique=()):
    """Check required full keys only; unrelated indexes are outside this contract."""
    inspector = inspect(connection)
    _require(inspector.get_pk_constraint(table)["constrained_columns"] == ["id"], table + " PK(id)")
    for column in unique:
        rows = connection.execute(text(
            "SELECT INDEX_NAME,NON_UNIQUE,COLUMN_NAME,SUB_PART FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table ORDER BY INDEX_NAME,SEQ_IN_INDEX"
        ), {"table": table}).all()
        indexes = {}
        for name, non_unique, field, prefix in rows:
            indexes.setdefault(name, []).append((non_unique, field, prefix))
        _require([(0, column, None)] in indexes.values(), table + ".UNIQUE(" + column + ")")


def _dependency_column(columns, name, kind, table):
    actual = columns.get(name)
    _require(actual is not None, table + "." + name + " missing")
    actual_type = re.sub(r"^int\(\d+\)$", "int", actual["COLUMN_TYPE"].lower())
    _require(actual_type == kind and actual["IS_NULLABLE"] == "NO", table + "." + name + " type/nullable")


def _dependencies(connection):
    for table in ("users", "products"):
        # Only engine is required here; do not audit unrelated table attributes.
        engine = connection.execute(text(
            "SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table"
        ), {"table": table}).scalar()
        _require(engine == "InnoDB", table + " InnoDB")
        columns = _columns(connection, table)
        _dependency_column(columns, "id", "int", table)
        _required_indexes(connection, table, unique=("code",) if table == "products" else ())
        if table == "products":
            _dependency_column(columns, "code", "varchar(50)", table)
            _dependency_column(columns, "current_stock", "int", table)
    _dependency_column(_columns(connection, "reconciliation_lines"), "run_id", "int", "reconciliation_lines")
    _foreign_keys(inspect(connection), "reconciliation_lines",
                  [(("run_id",), "reconciliation_runs", ("id",))], only_column="run_id")


def _unparenthesize(tokens):
    """Remove only balanced parentheses enclosing the entire expression."""
    while tokens and tokens[0] == "(":
        depth = 0
        closing = None
        for index, token in enumerate(tokens):
            depth += (token == "(") - (token == ")")
            if depth == 0:
                closing = index
                break
        if closing != len(tokens) - 1:
            break
        tokens = tokens[1:-1]
    return tokens


def _status_domain(expression):
    # Narrow grammar: parenthesized identifier IN (parenthesized string literals).
    # SQL keyword/identifier case is irrelevant; literal case is NOT normalized.
    pattern = r"\s+|`[^`]+`|'(?:''|[^'])*'|[A-Za-z_][A-Za-z_0-9]*|[(),]"
    parts = re.findall(pattern, expression)
    _require("".join(parts) == expression, "unsupported status CHECK expression")
    tokens = [p if p.startswith("'") else p.strip("`").lower() for p in parts if not p.isspace()]
    tokens = _unparenthesize(tokens)
    _require(tokens.count("in") == 1, "unsupported status CHECK grammar")
    pivot = tokens.index("in")
    _require(_unparenthesize(tokens[:pivot]) == ["status"], "status CHECK column")
    values = tokens[pivot + 1:]
    _require(values and values[0] == "(" and values[-1] == ")", "status CHECK list")
    groups = [[]]
    for token in values[1:-1]:
        if token == ",":
            groups.append([])
        else:
            groups[-1].append(token)
    domain = []
    for group in groups:
        literal = _unparenthesize(group)
        _require(len(literal) == 1 and literal[0].startswith("'"), "status CHECK literal")
        domain.append(literal[0])
    return set(domain)


def _run_status_check(connection):
    checks = dict(connection.execute(text(
        "SELECT CONSTRAINT_NAME,CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='reconciliation_runs'"
    )).all())
    _require("ck_reconciliation_status" in checks, "reconciliation_runs status CHECK missing")
    _require(_status_domain(checks["ck_reconciliation_status"]) ==
             {"'preview'", "'confirmed'", "'discarded'"}, "reconciliation_runs status CHECK domain")


def _table(connection, table, columns, indexes, foreign_keys, checks):
    _table_storage(connection, table)
    actual = _columns(connection, table)
    _require(set(actual) == set(columns), table + " column set")
    for name, expected in columns.items():
        _column(actual[name], expected, table + "." + name)
    _indexes(connection, table, indexes)
    _foreign_keys(inspect(connection), table, foreign_keys)
    _checks(connection, table, checks)


def _verify_target(connection, database):
    _require(connection.dialect.name == "mysql" and connection.dialect.is_mariadb, "MariaDB target required")
    _require(connection.execute(text("SELECT DATABASE()")).scalar() == database, "connected database mismatch")
    _require(connection.execute(text("SELECT @@session.check_constraint_checks, @@session.foreign_key_checks")).one()
             == (1, 1), "constraint enforcement must be enabled")


def verify_schema(connection, database, *, final=True):
    """Re-read REAL metadata; final=False permits only documented resumable states."""
    _verify_target(connection, database)
    inspector = inspect(connection)
    for table in ("products", "users", "reconciliation_runs", "reconciliation_lines"):
        _require(inspector.has_table(table), table + " required")
    _dependencies(connection)
    _table_storage(connection, "reconciliation_runs")
    runs = _columns(connection, "reconciliation_runs")
    for name, expected in {"id": ("int", False, "auto_increment"),
                           "number": ("varchar(20)", True, ""),
                           "status": ("varchar(20)", False, "")}.items():
        _column(runs.get(name), expected, "reconciliation_runs." + name)
    _required_indexes(connection, "reconciliation_runs", unique=("number",))
    _run_status_check(connection)
    _column(runs.get("created_by_user_id"), ("int", True, ""),
            "reconciliation_runs.created_by_user_id", allow_not_null=not final)
    _foreign_keys(inspector, "reconciliation_runs", [(("created_by_user_id",), "users", ("id",))],
                  only_column="created_by_user_id")
    if final or "source_type" in runs:
        _column(runs.get("source_type"), ("varchar(12)", True, ""), "reconciliation_runs.source_type")
    specs = (
        ("reconciliation_cuts", CUT_COLUMNS,
         [(True, True, ("id",)), (False, True, ("source", "scheduled_for")), (False, True, ("run_id",))],
         [(("run_id",), "reconciliation_runs", ("id",))], CHECKS),
        ("reconciliation_cut_stock", STOCK_COLUMNS,
         [(True, True, ("cut_id", "product_id")), (False, False, ("product_code_snapshot",))],
         [(("cut_id",), "reconciliation_cuts", ("id",))], {}),
    )
    for table, columns, indexes, foreign_keys, checks in specs:
        if final or inspector.has_table(table):
            _table(connection, table, columns, indexes, foreign_keys, checks)


def migrate(engine, database: str, *, authorize_thesis: bool = False) -> None:
    validate_target(database, authorize_thesis)
    if engine.url.database != database:
        raise RuntimeError("Engine target mismatch; no connection allowed.")
    with engine.connect() as connection:
        # Inspect every existing object BEFORE the first DDL. There is no global
        # DDL rollback: successful steps persist and a later invocation resumes.
        verify_schema(connection, database, final=False)
        columns = _columns(connection, "reconciliation_runs")
        if "source_type" not in columns:
            connection.execute(text("ALTER TABLE reconciliation_runs ADD COLUMN source_type VARCHAR(12) NULL"))
        if columns["created_by_user_id"]["IS_NULLABLE"] == "NO":
            connection.execute(text("ALTER TABLE reconciliation_runs MODIFY COLUMN created_by_user_id INT NULL"))
        for table, ddl in (("reconciliation_cuts", CREATE_CUTS), ("reconciliation_cut_stock", CREATE_STOCK)):
            if not inspect(connection).has_table(table):
                connection.execute(text(ddl))
        verify_schema(connection, database)
        connection.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--authorize-thesis", action="store_true")
    args = parser.parse_args()
    validate_target(args.database, args.authorize_thesis)
    if not args.apply:
        print("PLAN ONLY: nullable run provenance/actor and new cut/stock tables. No connection or mutation.")
        return
    engine = engine_for(args.database)
    try:
        migrate(engine, args.database, authorize_thesis=args.authorize_thesis)
        print("PASS: cut schema ready. No backfill or stock modification.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
