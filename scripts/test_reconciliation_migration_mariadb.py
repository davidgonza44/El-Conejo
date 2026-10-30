"""Real migration tests on a NEW private MariaDB server; never use an existing DB.

python -B scripts/test_reconciliation_migration_mariadb.py
Uses installed Windows MariaDB binaries, random loopback port and fresh datadir.
No application imports, dotenv, scheduler, business data or db.create_all().
"""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import URL

import migrate_reconciliation_cuts as migration
from test_reconciliation_disposable_server import BIN, ROOT, wait_ready


LEGACY_RUN = """CREATE TABLE reconciliation_runs (
 id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, number VARCHAR(20) NULL UNIQUE,
 cutoff_at DATETIME NOT NULL, imported_at DATETIME NOT NULL, filename VARCHAR(255) NOT NULL,
 sha256 VARCHAR(64) NOT NULL, created_by_user_id INT NOT NULL,
 status VARCHAR(20) NOT NULL, received_count INT NOT NULL, valid_count INT NOT NULL,
 invalid_count INT NOT NULL, match_count INT NOT NULL, discrepancy_count INT NOT NULL,
 unmatched_count INT NOT NULL,
 FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT ck_reconciliation_status CHECK (status IN ('preview','confirmed','discarded'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
LEGACY_LINES = """CREATE TABLE reconciliation_lines (
 id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, run_id INT NOT NULL, row_number INT NOT NULL,
 product_code VARCHAR(50) NOT NULL, product_name VARCHAR(150) NULL, product_id INT NULL,
 external_quantity INT NULL, system_quantity INT NULL, difference INT NULL,
 result VARCHAR(20) NOT NULL, error VARCHAR(255) NULL, review_status VARCHAR(20) NOT NULL,
 reviewed_by_user_id INT NULL, reviewed_at DATETIME NULL, review_note VARCHAR(1000) NULL,
 resolved_by_user_id INT NULL, resolved_at DATETIME NULL, resolution_note VARCHAR(1000) NULL,
 resolution_action VARCHAR(20) NULL, stock_movement_id INT NULL UNIQUE,
 CONSTRAINT uq_reconciliation_row UNIQUE(run_id,row_number),
 CONSTRAINT ck_reconciliation_result CHECK(result IN ('invalid','unmatched','match','discrepancy')),
 CONSTRAINT ck_reconciliation_review CHECK(review_status IN ('pending','reviewed','resolved')),
 CONSTRAINT ck_reconciliation_quantity CHECK(external_quantity IS NULL OR external_quantity >= 0),
 CONSTRAINT ck_reconciliation_action CHECK(resolution_action IS NULL OR resolution_action IN ('acknowledge','adjustment')),
 FOREIGN KEY(run_id) REFERENCES reconciliation_runs(id),
 FOREIGN KEY(product_id) REFERENCES products(id),
 FOREIGN KEY(reviewed_by_user_id) REFERENCES users(id),
 FOREIGN KEY(resolved_by_user_id) REFERENCES users(id),
 FOREIGN KEY(stock_movement_id) REFERENCES stock_movements(id), INDEX(product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
SENTINELS = ("products", "stock_movements", "sales", "purchase_orders", "purchase_receptions",
             "inventory_snapshots", "predictive_results")


def execute(engine, *statements):
    with engine.begin() as connection:
        for sql in statements:
            connection.execute(text(sql))


def baseline(engine):
    execute(engine, "CREATE TABLE users(id INT PRIMARY KEY) ENGINE=InnoDB",
            "INSERT INTO users VALUES(1)",
            "CREATE TABLE products(id INT PRIMARY KEY, code VARCHAR(50) NOT NULL UNIQUE, "
            "current_stock INT NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
            "INSERT INTO products VALUES(1,'SYNTHETIC',17)")
    for table in SENTINELS[1:]:
        execute(engine, f"CREATE TABLE {table}(id INT PRIMARY KEY, value INT NOT NULL) ENGINE=InnoDB",
                f"INSERT INTO {table} VALUES(1,17)")
    execute(engine, LEGACY_RUN, LEGACY_LINES,
            "INSERT INTO reconciliation_runs VALUES(1,'SYNTHETIC','2026-01-01','2026-01-01',"
            "'synthetic.csv','synthetic',1,'confirmed',1,1,0,1,0,0)",
            "INSERT INTO reconciliation_lines(id,run_id,row_number,product_code,product_id,external_quantity,"
            "system_quantity,difference,result,review_status) VALUES(1,1,1,'SYNTHETIC',1,17,17,0,'match','pending')")


def snapshot(engine):
    with engine.connect() as connection:
        tables = SENTINELS + ("reconciliation_lines",)
        state = {table: connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).all() for table in tables}
        state["runs"] = connection.execute(text(
            "SELECT id,number,cutoff_at,imported_at,filename,sha256,created_by_user_id,status,"
            "received_count,valid_count,invalid_count,match_count,discrepancy_count,unmatched_count "
            "FROM reconciliation_runs ORDER BY id")).all()
        return state


def schema(engine):
    with engine.connect() as connection:
        return {table: connection.execute(text(f"SHOW CREATE TABLE `{table}`")).one()[1]
                for table in inspect(connection).get_table_names()}


def expect_rejection(engine, name, fragment, verifier=False):
    before = schema(engine)
    ddl = []
    def record(_c, _cur, statement, _p, _ctx, _many):
        if statement.lstrip().upper().startswith(("ALTER ", "CREATE ", "DROP ")):
            ddl.append(statement)
    event.listen(engine, "before_cursor_execute", record)
    try:
        try:
            if verifier:
                with engine.connect() as connection:
                    migration.verify_schema(connection, name)
            else:
                migration.migrate(engine, name)
        except RuntimeError as error:
            assert fragment in str(error), str(error)
        else:
            raise AssertionError("Incompatible schema returned success")
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert not ddl, "Rejected preflight emitted DDL"
    assert before == schema(engine), "Rejected preflight changed schema"


def success(engine, name):
    before = snapshot(engine)
    migration.migrate(engine, name)
    with engine.connect() as connection:
        migration.verify_schema(connection, name)
        assert connection.execute(text("SELECT source_type FROM reconciliation_runs")).scalar() is None
    assert before == snapshot(engine), "Historical/business sentinel changed"


def twice(engine, name):
    success(engine, name)
    before = schema(engine)
    ddl = []
    def record(_c, _cur, statement, _p, _ctx, _many):
        if statement.lstrip().upper().startswith(("ALTER ", "CREATE ", "DROP ")):
            ddl.append(statement)
    event.listen(engine, "before_cursor_execute", record)
    try:
        success(engine, name)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert not ddl and schema(engine) == before, "Second migration emitted DDL or changed schema"


def partial(engine, name):
    execute(engine, "ALTER TABLE reconciliation_runs ADD source_type VARCHAR(12) NULL",
            "ALTER TABLE reconciliation_runs MODIFY created_by_user_id INT NULL", migration.CREATE_CUTS)
    success(engine, name)


def interrupted(engine, name):
    def fail_after_alter(_c, _cur, statement, _p, _ctx, _many):
        if statement.startswith("ALTER TABLE reconciliation_runs MODIFY"):
            raise RuntimeError("Injected after committed ALTER")
    event.listen(engine, "after_cursor_execute", fail_after_alter)
    try:
        try:
            migration.migrate(engine, name)
        except RuntimeError as error:
            assert "Injected" in str(error)
        else:
            raise AssertionError("Failure injection not executed")
    finally:
        event.remove(engine, "after_cursor_execute", fail_after_alter)
    with engine.connect() as connection:
        columns = migration._columns(connection, "reconciliation_runs")
        assert "source_type" in columns and columns["created_by_user_id"]["IS_NULLABLE"] == "YES"
        assert not inspect(connection).has_table("reconciliation_cuts")
    twice(engine, name)


def malformed(engine, name, mutation, fragment, final=False):
    success(engine, name)
    execute(engine, *mutation)
    expect_rejection(engine, name, fragment, verifier=final)


def reject_legacy(engine, name, mutation, fragment):
    execute(engine, *mutation)
    expect_rejection(engine, name, fragment)


def replace_fk(engine, table, column, replacement=None):
    with engine.connect() as connection:
        fk = next(fk["name"] for fk in inspect(connection).get_foreign_keys(table)
                  if fk["constrained_columns"] == [column])
    statement = f"ALTER TABLE `{table}` DROP FOREIGN KEY `{fk}`"
    if replacement:
        statement += ", ADD " + replacement
    execute(engine, statement)


def wrong_legacy_fk(engine, name, table, column, replacement, fragment):
    replace_fk(engine, table, column, replacement)
    expect_rejection(engine, name, fragment)


def equivalent_status(engine, name):
    execute(engine, "ALTER TABLE reconciliation_runs DROP CONSTRAINT ck_reconciliation_status, "
            "ADD CONSTRAINT ck_reconciliation_status CHECK (((`STATUS`) iN "
            "(('discarded'), ('preview'), ('confirmed'))))")
    twice(engine, name)


def legacy_cases():
    cases = [
        ("A compatible legacy preflight", lambda e,n: preflight(e,n)),
        ("B runs missing PK", ["ALTER TABLE reconciliation_runs ADD INDEX keep_id(id), DROP PRIMARY KEY"], "reconciliation_runs PK"),
        ("C runs missing UNIQUE(number)", ["ALTER TABLE reconciliation_runs DROP INDEX number"], "UNIQUE(number)"),
        ("D runs wrong UNIQUE", ["ALTER TABLE reconciliation_runs DROP INDEX number, ADD UNIQUE(filename)"], "UNIQUE(number)"),
        ("E runs missing status CHECK", ["ALTER TABLE reconciliation_runs DROP CONSTRAINT ck_reconciliation_status"], "status CHECK missing"),
        ("F runs wrong status domain", ["ALTER TABLE reconciliation_runs DROP CONSTRAINT ck_reconciliation_status, "
         "ADD CONSTRAINT ck_reconciliation_status CHECK(status IN ('preview','confirmed','other'))"], "status CHECK domain"),
        ("G equivalent status CHECK", equivalent_status),
        ("H missing actor FK", missing_actor_fk),
        ("I actor CASCADE rejected", lambda e,n: wrong_legacy_fk(e,n,"reconciliation_runs","created_by_user_id",
         "FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON UPDATE CASCADE", "FK actions")),
        ("J products missing UNIQUE(code)", ["ALTER TABLE products DROP INDEX code"], "UNIQUE(code)"),
        ("K users missing PK", ["ALTER TABLE users ADD INDEX keep_id(id), DROP PRIMARY KEY"], "users PK"),
        ("L lines incompatible FK", lambda e,n: wrong_legacy_fk(e,n,"reconciliation_lines","run_id",
         "FOREIGN KEY(run_id) REFERENCES users(id)", "reconciliation_lines foreign keys")),
        ("M runs prefix UNIQUE", ["ALTER TABLE reconciliation_runs DROP INDEX number, ADD UNIQUE(number(10))"], "UNIQUE(number)"),
        ("N unsupported status expression", ["ALTER TABLE reconciliation_runs DROP CONSTRAINT ck_reconciliation_status, "
         "ADD CONSTRAINT ck_reconciliation_status CHECK(status IN ('preview','confirmed','discarded') OR 1=1)"], "unsupported status CHECK"),
        ("O products wrong code type", ["ALTER TABLE products MODIFY code VARCHAR(49) NOT NULL"], "products.code type"),
        ("P products unsigned stock", ["ALTER TABLE products MODIFY current_stock INT UNSIGNED NOT NULL"], "products.current_stock type"),
        ("Q final verifier checks runs UNIQUE", lambda e,n: malformed(e,n,
         ["ALTER TABLE reconciliation_runs DROP INDEX number"], "UNIQUE(number)", True)),
    ]
    return [(case[0], case[1] if len(case) == 2 else
             lambda e,n,mutation=case[1],fragment=case[2]: reject_legacy(e,n,mutation,fragment)) for case in cases]


def preflight(engine, name):
    before = schema(engine)
    with engine.connect() as connection:
        connection.execute(text("START TRANSACTION READ ONLY"))
        migration.verify_schema(connection, name, final=False)
        connection.rollback()
    assert schema(engine) == before


def suite(server_url, datadir, port):
    server = create_engine(server_url)
    cases = [
        ("1 baseline migration + real verifier + unchanged synthetic history", success),
        ("2 double execution: zero DDL and identical schema", twice),
        ("3 partial correct cuts table, missing stock table", partial),
        ("4 missing UNIQUE rejects without DDL", lambda e,n: malformed(e,n,
            ["ALTER TABLE reconciliation_cuts DROP INDEX uq_reconciliation_cut_source_time"], "index contract")),
        ("5 incompatible type rejects", lambda e,n: malformed(e,n,
            ["ALTER TABLE reconciliation_cuts MODIFY source VARCHAR(49) NOT NULL"], "type/length")),
        ("6a incompatible PK rejects", lambda e,n: malformed(e,n,
            ["ALTER TABLE reconciliation_cut_stock DROP PRIMARY KEY, ADD PRIMARY KEY(cut_id,product_id,quantity)"], "index contract")),
        ("6b incompatible FK action rejects", wrong_fk),
        ("7a missing CHECK rejects", lambda e,n: malformed(e,n,
            ["ALTER TABLE reconciliation_cuts DROP CONSTRAINT ck_reconciliation_cut_status"], "CHECK constraints")),
        ("7b incompatible CHECK rejects", lambda e,n: malformed(e,n,
            ["ALTER TABLE reconciliation_cuts DROP CONSTRAINT ck_reconciliation_cut_status, "
             "ADD CONSTRAINT ck_reconciliation_cut_status CHECK(status IN ('waiting_source ','completed','failed'))"], "ck_reconciliation_cut_status")),
        ("8 interrupted DDL resumes then no-op replay", interrupted),
        ("9 post-verifier independently detects missing CHECK", lambda e,n: malformed(e,n,
            ["ALTER TABLE reconciliation_cuts DROP CONSTRAINT ck_reconciliation_snapshot_status"], "CHECK constraints", True)),
        ("10 unexpected actor default aborts before any ALTER", wrong_actor),
        ("10b missing actor FK aborts before any ALTER", missing_actor_fk),
        ("10c incompatible source_type aborts before any ALTER", wrong_source_type),
        ("11 prefix UNIQUE rejected", lambda e,n: malformed(e,n,
            ["ALTER TABLE reconciliation_cuts DROP INDEX uq_reconciliation_cut_source_time, "
             "ADD UNIQUE uq_reconciliation_cut_source_time(source(10),scheduled_for)"], "index prefix")),
        ("12 forbidden product FK rejected", lambda e,n: malformed(e,n,
            ["ALTER TABLE reconciliation_cut_stock ADD FOREIGN KEY(product_id) REFERENCES products(id)"], "index contract")),
    ]
    cases = legacy_cases() + cases
    try:
        with server.connect() as connection:
            actual = connection.execute(text("SELECT @@datadir,@@port,DATABASE(),VERSION()")).one()
            assert Path(actual[0]).resolve() == datadir.resolve() and actual[1] == port
            assert actual[2] is None and connection.dialect.is_mariadb
            print("SERVER:", actual[3])
        for label, case in cases:
            name = "reconciliation_test_" + secrets.token_hex(6)
            migration.validate_target(name)
            created = False
            engine = create_engine(server_url.set(database=name))
            try:
                with server.begin() as connection:
                    assert not connection.execute(text(
                        "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=:name"), {"name": name}).first()
                    connection.execute(text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4"))
                    created = True
                baseline(engine)
                before = snapshot(engine)
                case(engine, name)
                assert before == snapshot(engine)
                print("PASS:", label, flush=True)
            finally:
                engine.dispose()
                if created:
                    with server.begin() as connection:
                        assert connection.execute(text("SELECT DATABASE()")).scalar() is None
                        connection.execute(text(f"DROP DATABASE `{name}`"))
    finally:
        server.dispose()


def wrong_actor(engine, name):
    execute(engine, "ALTER TABLE reconciliation_runs MODIFY created_by_user_id INT NOT NULL DEFAULT 1")
    expect_rejection(engine, name, "server default")


def missing_actor_fk(engine, name):
    with engine.connect() as connection:
        fk = inspect(connection).get_foreign_keys("reconciliation_runs")[0]["name"]
    execute(engine, f"ALTER TABLE reconciliation_runs DROP FOREIGN KEY `{fk}`")
    expect_rejection(engine, name, "foreign keys")


def wrong_source_type(engine, name):
    execute(engine, "ALTER TABLE reconciliation_runs ADD source_type VARCHAR(20) NULL")
    expect_rejection(engine, name, "type/length")


def wrong_fk(engine, name):
    success(engine, name)
    with engine.connect() as connection:
        fk = inspect(connection).get_foreign_keys("reconciliation_cuts")[0]["name"]
    execute(engine, f"ALTER TABLE reconciliation_cuts DROP FOREIGN KEY `{fk}`, "
            "ADD FOREIGN KEY(run_id) REFERENCES reconciliation_runs(id) ON DELETE CASCADE")
    expect_rejection(engine, name, "FK actions")


def safety():
    assert "app" not in sys.modules and "dotenv" not in sys.modules
    for name in ("ferreteria_conejo_tesis", "mysql", "reconciliation_test_invalid"):
        try:
            migration.validate_target(name)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Unsafe target accepted")
    with patch.object(migration, "engine_for", side_effect=AssertionError("Dry run connected")), \
            patch.object(sys, "argv", ["migration", "--database", "reconciliation_test_123456abcdef"]):
        migration.main()
    print("PASS: target allowlist, no app/dotenv import and dry run without connection")


def main():
    safety()
    assert os.name == "nt"
    assert all((BIN / filename).is_file() for filename in ("mysqld.exe", "mysql_install_db.exe"))
    directory = ROOT / "tmp" / ("reconciliation-server-" + secrets.token_hex(6))
    directory.mkdir(parents=True, exist_ok=False)
    datadir = directory / "data"
    password = secrets.token_urlsafe(24)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    assert port != 3306
    flags = subprocess.CREATE_NO_WINDOW
    with (directory / "initialization.log").open("xb") as output:
        result = subprocess.run([str(BIN / "mysql_install_db.exe"), "--datadir=" + str(datadir),
                                 "--password=" + password, "--port=" + str(port)],
                                stdout=output, stderr=subprocess.STDOUT, creationflags=flags, check=False)
    assert result.returncode == 0, "Private server initialization failed"
    with (directory / "server.log").open("xb") as output:
        process = subprocess.Popen([str(BIN / "mysqld.exe"), "--no-defaults", "--datadir=" + str(datadir),
                                    "--port=" + str(port), "--bind-address=127.0.0.1", "--skip-log-bin",
                                    "--innodb-buffer-pool-size=32M", "--console"],
                                   stdout=output, stderr=subprocess.STDOUT, creationflags=flags)
        try:
            wait_ready(process, datadir, port, password)
            suite(URL.create("mysql+pymysql", username="root", password=password,
                             host="127.0.0.1", port=port, query={"charset": "utf8mb4"}), datadir, port)
        finally:
            process.terminate()
            process.wait(timeout=20)
            print("Private test process stopped; only synthetic datadir retained, no service/task installed.")


if __name__ == "__main__":
    main()
