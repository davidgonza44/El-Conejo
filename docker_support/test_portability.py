"""Synthetic filesystem and scheduling tests. No database is accessed."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.services.reconciliation_file_service import rename_new, transport, digest
from app.services.portable_scheduler_service import next_run, ZONE


class PortabilityTests(unittest.TestCase):
    def test_guard_blocks_valuable_database_before_connecting(self):
        from docker_support.guard import wait_database
        with patch.dict(os.environ, {"DOCKER_ENV_KIND": "disposable", "DB_NAME": "ferreteria_conejo"}):
            with patch("docker_support.guard.pymysql.connect") as connect:
                with self.assertRaises(RuntimeError):
                    wait_database()
                connect.assert_not_called()

    def test_runtime_image_boundary(self):
        self.assertEqual(sys.version_info[:2], (3, 12))
        self.assertEqual(os.getuid(), 10001)
        forbidden = {"venv", ".venv", "venv312", "private_imports", ".git"}
        for path in Path("/app").rglob("*"):
            self.assertFalse(forbidden.intersection(path.parts), str(path))
            self.assertFalse(path.name.startswith(".env"), str(path))
        self.assertEqual(list(Path("/app/uploads").iterdir()), [])
        self.assertEqual(list(Path("/app/instance").iterdir()), [])

    def test_publication_race_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            final = root / "complete.csv"
            sources = [root / f"{i}.tmp" for i in range(8)]
            for i, source in enumerate(sources):
                source.write_bytes(str(i).encode() * 1024)

            def publish(source):
                try:
                    rename_new(source, final)
                    return source
                except FileExistsError:
                    return None

            with ThreadPoolExecutor(max_workers=8) as pool:
                winners = [item for item in pool.map(publish, sources) if item]
            self.assertEqual(len(winners), 1)
            self.assertEqual(final.read_bytes(), winners[0].stem.encode() * 1024)
            self.assertEqual(sum(item.exists() for item in sources), 7)

    def test_transport_retry_preserves_content(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "exports").mkdir()
            source = root / "exports" / "inventario_2030-01-01_0800.csv"
            source.write_bytes(b"product_code;quantity\nSYN-1;3\n")
            inbox = root / "inbox"
            transport(source, inbox)
            transport(source, inbox)
            self.assertEqual(digest(source), digest(inbox / source.name))
            source.write_bytes(b"changed source")
            with self.assertRaises(ValueError):
                transport(source, inbox)
            self.assertIn(b"SYN-1", (inbox / source.name).read_bytes())

    def test_schedule_calendar(self):
        after = datetime(2026, 10, 3, 12, tzinfo=ZONE)
        self.assertEqual(next_run({"frequency": "daily", "time": "02:00"}, after).day, 4)
        self.assertEqual(next_run({"frequency": "weekly", "time": "02:00"}, after).day, 5)
        monthly = next_run({"frequency": "monthly", "time": "02:00"}, after)
        self.assertEqual((monthly.month, monthly.day), (11, 1))
        self.assertIsNone(next_run({"frequency": "off"}, after))

    def test_predictive_native_dependencies(self):
        from app.services import forecast_advanced_service as forecast
        history = [float(8 + (i % 7) + i % 3) for i in range(120)]
        starts = [date(2025, 1, 1) + timedelta(days=i) for i in range(120)]
        for model in (forecast.arima, forecast.random_forest):
            path = model(history, 7, "daily", starts)
            self.assertEqual(len(path), 7)
            self.assertTrue(all(math.isfinite(value) and value >= 0 for value in path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
