"""Windows: run the snapshot suite on a private temporary MariaDB process.

No existing database/server configuration is used. No Windows service or task is
installed. Only a newly created datadir is initialized; it is retained for audit.
"""
import argparse
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

import pymysql

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(r"C:\xampp\mysql\bin")


def connect(port, password):
    return pymysql.connect(host="127.0.0.1", port=port, user="root", password=password, connect_timeout=1)


def wait_ready(process, directory, port, password):
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("Disposable MariaDB stopped before readiness; inspect its own server.log.")
        try:
            connection = connect(port, password)
        except pymysql.OperationalError:
            time.sleep(0.2)
            continue
        with connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT @@datadir, @@port, DATABASE()")
                datadir, actual_port, database = cursor.fetchone()
                assert Path(datadir).resolve() == directory.resolve()
                assert actual_port == port and database is None
        return
    raise RuntimeError("Disposable MariaDB did not become ready; no suite was run.")


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file-pipeline', action='store_true')
    parser.add_argument('--landing-pipeline', action='store_true')
    parser.add_argument('--exports-root')
    parser.add_argument('--inbox-root')
    parser.add_argument('--physical-manifest')
    args = parser.parse_args()
    suite = 'test_reconciliation_automatic_mariadb.py'
    extra = []
    if args.file_pipeline and args.landing_pipeline:
        parser.error('Choose only one pipeline suite')
    if args.file_pipeline or args.landing_pipeline:
        if not args.exports_root or not args.inbox_root:
            parser.error('The file pipeline requires both explicit synthetic roots')
        suite = 'test_reconciliation_file_pipeline.py'
        if args.landing_pipeline:
            suite = 'test_reconciliation_landing_pipeline.py'
        extra = ['--exports-root', args.exports_root, '--inbox-root', args.inbox_root]
        if args.physical_manifest:
            extra += ['--physical-manifest', args.physical_manifest]
    assert os.name == "nt", "This harness requires the installed Windows MariaDB binaries"
    for executable in ("mysqld.exe", "mysql_install_db.exe"):
        assert (BIN / executable).is_file(), "MariaDB test binary missing"
    directory = ROOT / "tmp" / ("reconciliation-server-" + secrets.token_hex(6))
    directory.mkdir(parents=True, exist_ok=False)
    datadir = directory / "data"  # Initialization requires an empty directory, without our logs.
    password = secrets.token_urlsafe(24)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    assert port != 3306
    creationflags = subprocess.CREATE_NO_WINDOW
    with (directory / "initialization.log").open("xb") as output:
        init = subprocess.run([str(BIN / "mysql_install_db.exe"), "--datadir=" + str(datadir),
                               "--password=" + password, "--port=" + str(port)],
                              stdout=output, stderr=subprocess.STDOUT, creationflags=creationflags, check=False)
    assert init.returncode == 0, "Disposable initialization failed; no existing server was used"
    with (directory / "server.log").open("xb") as output:
        process = subprocess.Popen([str(BIN / "mysqld.exe"), "--no-defaults", "--datadir=" + str(datadir),
                                    "--port=" + str(port), "--bind-address=127.0.0.1", "--skip-log-bin",
                                    "--innodb-buffer-pool-size=32M", "--console"],
                                   stdout=output, stderr=subprocess.STDOUT, creationflags=creationflags)
        try:
            wait_ready(process, datadir, port, password)
            environment = os.environ.copy()
            environment.update(RUN_RECONCILIATION_MARIADB_TESTS="1", TEST_MARIADB_USER="root",
                               TEST_MARIADB_PASSWORD=password, TEST_MARIADB_PORT=str(port),
                               TEST_MARIADB_DATADIR=str(datadir), TEST_MARIADB_HOST="127.0.0.1")
            print("Private disposable MariaDB verified; separate datadir and loopback port; no existing server used.", flush=True)
            result = subprocess.run([sys.executable, "-B", "-W", "ignore::DeprecationWarning",
                                     str(ROOT / "scripts" / suite), *extra],
                                    env=environment, check=False)
            return result.returncode
        finally:
            # Stop only the process handle created above, never a named system service.
            process.terminate()
            process.wait(timeout=20)
            print("Disposable MariaDB process stopped. Only synthetic files retained in", directory)


if __name__ == "__main__":
    raise SystemExit(run())
