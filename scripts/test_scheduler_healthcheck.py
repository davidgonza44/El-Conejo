"""Synthetic, stdlib-only scheduler healthcheck checks; no Flask or database.

Run: python -B scripts/test_scheduler_healthcheck.py
All fixtures, CLI packages and permission changes stay in TemporaryDirectory.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
from threading import Barrier
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from docker_support import scheduler_healthcheck as health  # noqa: E402


NOW = datetime(2030, 1, 2, 12, tzinfo=timezone.utc)


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz)


class SchedulerHealthcheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="scheduler-healthcheck-")
        self.root = Path(self.temporary.name)
        self.path = self.root / "instance" / "scheduler" / "worker.json"
        self.path.parent.mkdir(parents=True)
        self.path_patch = patch.object(health, "HEARTBEAT_PATH", self.path)
        self.clock_patch = patch.object(health, "datetime", FixedClock)
        self.path_patch.start()
        self.clock_patch.start()

    def tearDown(self):
        self.clock_patch.stop()
        self.path_patch.stop()
        self.temporary.cleanup()

    def write(self, value):
        self.path.write_text(json.dumps({"heartbeat": value}), encoding="utf-8")

    def prepare_cli(self):
        package = self.root / "docker_support"
        package.mkdir()
        for name in ("__init__.py", "scheduler_healthcheck.py"):
            shutil.copyfile(ROOT / "docker_support" / name, package / name)
        # Deliberately omit all application and predictive dependencies.
        environment = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
        if os.name == "nt":
            environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        return environment

    def run_cli(self, environment, *arguments):
        return subprocess.run(
            [sys.executable, *arguments], cwd=self.root, env=environment,
            capture_output=True, text=True, timeout=4, check=False,
        )

    def test_recent_heartbeat_and_timezone_offsets(self):
        for zone in (timezone.utc, timezone(timedelta(hours=-4)),
                     timezone(timedelta(hours=5, minutes=30))):
            with self.subTest(zone=str(zone)):
                self.write((NOW - timedelta(seconds=20)).astimezone(zone).isoformat())
                self.assertEqual(health.main(), 0)
        self.write((NOW - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"))
        self.assertEqual(health.main(), 0)

    def test_age_boundaries_and_future_timestamps(self):
        cases = ((0, 0), (59.999999, 0), (60, 1), (61, 1),
                 (3600, 1), (-0.000001, 1), (-20, 1), (-86400, 1))
        for age, expected in cases:
            with self.subTest(age=age):
                self.write((NOW - timedelta(seconds=age)).isoformat())
                self.assertEqual(health.main(), expected)

    def test_missing_file_and_directory(self):
        self.assertEqual(health.main(), 1)
        self.path.mkdir()
        self.assertEqual(health.main(), 1)

    def test_corrupt_json_and_invalid_encoding(self):
        for data in (b"{", b"", b"not JSON", b"\xff", b"[" * 2000):
            with self.subTest(data=data[:10]):
                self.path.write_bytes(data)
                self.assertEqual(health.main(), 1)

    def test_invalid_json_shapes_and_timestamp_types(self):
        records = (None, [], "text", 7, {}, {"heartbeat": None},
                   {"heartbeat": 123}, {"heartbeat": []}, {"heartbeat": {}})
        for record in records:
            with self.subTest(record=record):
                self.path.write_text(json.dumps(record), encoding="utf-8")
                self.assertEqual(health.main(), 1)

    def test_invalid_or_naive_timestamp(self):
        for value in ("", "invalid", "2030-01-02", "2030-01-02T12:00:00",
                      "2030-13-02T12:00:00+00:00", "2030-01-02T12:00:00+99:00"):
            with self.subTest(value=value):
                self.write(value)
                self.assertEqual(health.main(), 1)

    def test_permission_error_is_unhealthy(self):
        with patch.object(Path, "read_text", side_effect=PermissionError):
            self.assertEqual(health.main(), 1)

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0,
                         "real mode bits require a non-root POSIX runtime")
    def test_real_unreadable_file(self):
        self.write(NOW.isoformat())
        self.path.chmod(0)
        try:
            self.assertEqual(health.main(), 1)
            with self.assertRaises(PermissionError):
                self.path.read_bytes()
        finally:
            self.path.chmod(0o600)

    def test_no_file_content_metadata_or_directory_changes(self):
        for value, expected in ((NOW.isoformat(), 0), ("invalid", 1)):
            self.write(value)
            before = self.path.stat()
            content = self.path.read_bytes()
            entries = sorted(self.root.rglob("*"))
            self.assertEqual(health.main(), expected)
            after = self.path.stat()
            self.assertEqual(self.path.read_bytes(), content)
            for attribute in ("st_mtime_ns", "st_ctime_ns", "st_size", "st_ino", "st_mode"):
                self.assertEqual(getattr(before, attribute), getattr(after, attribute))
            self.assertEqual(sorted(self.root.rglob("*")), entries)

    def test_concurrent_atomic_updates(self):
        self.write((NOW - timedelta(seconds=20)).isoformat())
        barrier = Barrier(5)
        read_errors = []
        read_text = Path.read_text

        def observed_read(path, *args, **kwargs):
            try:
                return read_text(path, *args, **kwargs)
            except OSError as error:
                read_errors.append(type(error))
                raise

        def writer():
            barrier.wait(timeout=4)
            for index in range(300):
                temporary = self.path.with_name(f"worker.json.{index}.tmp")
                temporary.write_text(json.dumps({
                    "heartbeat": (NOW - timedelta(seconds=1)).isoformat(),
                    "sequence": index,
                }), encoding="utf-8")
                deadline = time.perf_counter() + 3
                while True:
                    try:
                        temporary.replace(self.path)
                        break
                    except PermissionError:
                        # Windows can briefly hold a reader's file handle open.
                        if time.perf_counter() >= deadline:
                            raise
                        time.sleep(0.001)
                time.sleep(0.0005)
            return 300

        def reader():
            barrier.wait(timeout=4)
            results = []
            for _ in range(250):
                results.append(health.main())
                time.sleep(0.001)
            return results

        with patch.object(Path, "read_text", observed_read):
            with ThreadPoolExecutor(max_workers=5) as pool:
                updating = pool.submit(writer)
                readers = [pool.submit(reader) for _ in range(4)]
                self.assertEqual(updating.result(timeout=10), 300)
                results = [code for reading in readers for code in reading.result(timeout=10)]
        self.assertTrue(all(code in (0, 1) for code in results))
        self.assertEqual(results.count(1), len(read_errors))
        if os.name == "nt":
            # A file temporarily denied by Windows is correctly unhealthy.
            self.assertTrue(all(error is PermissionError for error in read_errors))
            print(f"\nCONCURRENCY Windows: {len(read_errors)} transient read denials / 1000 reads")
        else:
            self.assertEqual(read_errors, [])
            self.assertEqual(results, [0] * 1000)
        self.assertEqual(json.loads(self.path.read_text())["sequence"], 299)

    def test_default_path_uses_application_root_not_cwd(self):
        environment = self.prepare_cli()
        self.write((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat())
        environment["PYTHONPATH"] = str(self.root)
        result = subprocess.run(
            [sys.executable, "-m", "docker_support.scheduler_healthcheck"],
            cwd=self.path.parent, env=environment, capture_output=True,
            text=True, timeout=4, check=False,
        )
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_docker_command_exit_codes_without_sensitive_output(self):
        environment = self.prepare_cli()
        recent = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        cases = ((recent, 0), ("2000-01-01T00:00:00+00:00", 1),
                 ("2100-01-01T00:00:00+00:00", 1), ("private-synthetic-marker", 1))
        for value, expected in cases:
            self.write(value)
            result = self.run_cli(environment, "-m", "docker_support.scheduler_healthcheck")
            self.assertEqual((result.returncode, result.stdout, result.stderr),
                             (expected, "", ""))
        self.path.unlink()
        result = self.run_cli(environment, "-m", "docker_support.scheduler_healthcheck")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", ""))

    def test_stdlib_only_imports_and_no_write_or_network_audit_events(self):
        environment = self.prepare_cli()
        self.write((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat())
        guarded_run = '''
import importlib.abc
import os
import runpy
import sys

class StdlibOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split('.')[0]
        if root not in sys.stdlib_module_names and root != 'docker_support':
            raise AssertionError('non-stdlib import: ' + root)

def audit(event, args):
    if event == 'open':
        _, mode, flags = args
        if (mode and any(c in mode for c in 'wax+')) or flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError('write attempted')
    if event in ('os.mkdir', 'os.remove', 'os.rename', 'os.chmod', 'os.truncate',
                 'os.rmdir', 'os.link', 'os.symlink', 'os.utime'):
        raise AssertionError('filesystem mutation attempted')
    if event.startswith('socket.') or event == 'subprocess.Popen':
        raise AssertionError('external effect attempted')

sys.meta_path.insert(0, StdlibOnly())
sys.addaudithook(audit)
runpy.run_module('docker_support.scheduler_healthcheck', run_name='__main__')
'''
        result = self.run_cli(environment, "-S", "-B", "-c", guarded_run)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertFalse(list(self.root.rglob("__pycache__")))

    def test_cold_and_warm_execution_time(self):
        environment = self.prepare_cli()
        self.write((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat())
        samples = []
        for _ in range(11):
            start = time.perf_counter()
            result = self.run_cli(environment, "-m", "docker_support.scheduler_healthcheck")
            samples.append(time.perf_counter() - start)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertLess(max(samples), 1.0)
        self.write(NOW.isoformat())
        start = time.perf_counter()
        for _ in range(1000):
            self.assertEqual(health.main(), 0)
        warm = (time.perf_counter() - start) / 1000
        print(f"\nTIMING Python {sys.version.split()[0]}: first process={samples[0]:.6f}s; "
              f"10 repeated processes median={statistics.median(samples[1:]):.6f}s "
              f"max={max(samples[1:]):.6f}s; in-process mean={warm:.8f}s")


if __name__ == "__main__":
    unittest.main(verbosity=2)
