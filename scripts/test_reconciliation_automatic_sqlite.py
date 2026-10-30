"""Disposable SQLite contract tests; these do NOT prove MariaDB isolation or concurrency."""
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from approval_test_support import IsolatedCase
from app.extensions import db
from app.models import StockMovement, Notification
from app.models.reconciliation import ReconciliationCut, ReconciliationCutStock, ReconciliationRun
from app.services import reconciliation_automation_service as automation
from app.services import reconciliation_service, settings_service
from app.utils.schema import table_ready


class AutomaticContractTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.app.config.update(RECONCILIATION_INBOX_PATH="reconciliation_inbox",
                               RECONCILIATION_ARCHIVE_PATH="reconciliation_archive",
                               RECONCILIATION_ACTIVATED_ON="2026-09-30")
        # SQLite in-memory shares one DBAPI connection: metadata inspection must
        # precede business transactions, otherwise its close rolls that transaction back.
        for table in db.metadata.sorted_tables:
            self.assertTrue(table_ready(table.name))
        self.assertTrue(settings_service.is_available())

    def frozen_cut(self, *, status="captured"):
        cut = ReconciliationCut(source=automation.SOURCE,
                                scheduled_for=automation.scheduled_at(date(2026, 9, 30), "0800"),
                                snapshot_status=status)
        if status == "captured":
            cut.local_snapshot_at = datetime(2026, 9, 30, 12)
            cut.stock.append(ReconciliationCutStock(product_id=self.product.id,
                             product_code_snapshot=self.product.code, quantity=10))
        db.session.add(cut)
        db.session.commit()
        return cut

    def publish(self, cut, content=b"product_code;quantity\nTEST-01;14\n"):
        path = Path(self.app.instance_path) / "reconciliation_inbox" / automation.filename_for(cut)
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        return path

    def test_late_csv_idempotency_archive_and_immutable_stock(self):
        cut = self.frozen_cut()
        self.product.current_stock = 13
        db.session.commit()
        path = self.publish(cut)
        with patch.object(reconciliation_service, "_compare", side_effect=AssertionError("Live comparison forbidden")):
            result = automation.process_cut(cut.id)
            self.assertEqual(result.status, "completed")
            self.assertEqual((result.run.lines[0].system_quantity, result.run.lines[0].difference), (10, 4))
            run_id = result.run_id
            automation.process_cut(cut.id)
        self.assertEqual(ReconciliationRun.query.count(), 1)
        self.assertEqual(cut.run_id, run_id)
        self.assertEqual(cut.stock[0].quantity, 10)
        self.assertEqual(self.stock(), 13)
        self.assertEqual(StockMovement.query.count(), 0)
        self.assertFalse(path.exists())
        self.assertIsNotNone(cut.archived_at)
        self.assertIsNotNone(cut.source_received_at)
        self.assertEqual(cut.attempt_count, 1)

    def test_waiting_missing_and_lost_snapshot_never_use_live_stock(self):
        cut = self.frozen_cut(status="unavailable")
        path = self.publish(cut)
        with patch.object(reconciliation_service, "compare_frozen", side_effect=AssertionError("No snapshot")):
            self.assertIsNone(automation.process_cut(cut.id).run_id)
            cut.snapshot_status = "captured"  # Simulates missing frozen evidence, never reconstruct it.
            db.session.commit()
            self.assertIsNone(automation.process_cut(cut.id).run_id)
        self.assertEqual(cut.snapshot_status, "unavailable")
        self.assertEqual(cut.status, "failed")
        self.assertTrue(path.exists())
        self.assertEqual(self.stock(), 10)

    def test_pending_source_and_tmp_are_not_ingested(self):
        cut = self.frozen_cut()
        self.assertEqual(automation.process_cut(cut.id).status, "waiting_source")
        path = self.publish(cut)
        path.rename(path.with_suffix(".csv.tmp"))
        self.assertIsNone(automation.process_cut(cut.id).run_id)
        self.assertIsNone(cut.source_received_at)
        self.assertEqual(cut.attempt_count, 0)

    def test_failed_file_explicit_retry_keeps_frozen_quantity(self):
        cut = self.frozen_cut()
        self.publish(cut, b"wrong;columns\nx;1\n")
        automation.process_cut(cut.id)
        self.assertEqual(cut.status, "failed")
        self.assertEqual(cut.snapshot_status, "captured")
        self.assertEqual(cut.attempt_count, 1)
        self.product.current_stock = 99
        db.session.commit()
        self.publish(cut)
        self.assertIsNone(automation.process_cut(cut.id).run_id)  # No silent retries.
        result = automation.retry_failed(cut.id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.run.lines[0].system_quantity, 10)
        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(self.stock(), 99)

    def test_comparison_failure_rolls_back_run_and_lines(self):
        cut = self.frozen_cut()
        self.publish(cut)
        with patch.object(reconciliation_service, "_counts", side_effect=RuntimeError("Synthetic failure")):
            automation.process_cut(cut.id)
        self.assertEqual(cut.status, "failed")
        self.assertEqual(ReconciliationRun.query.count(), 0)
        self.assertEqual(cut.stock[0].quantity, 10)
        self.assertEqual(self.stock(), 10)

    def test_notification_and_cut_deduplication(self):
        cut = self.frozen_cut()
        self.assertEqual(automation.ensure_cut(date(2026, 9, 30), "0800").id, cut.id)
        automation.notify_cut(cut)
        before = Notification.query.filter_by(entity_type="reconciliation_cut").count()
        self.assertGreater(before, 0)
        automation.notify_cut(cut)
        self.publish(cut)
        automation.process_cut(cut.id)
        self.assertEqual(Notification.query.filter_by(entity_type="reconciliation_cut").count(), before)

    def test_archive_can_resume_without_recomparison(self):
        cut = self.frozen_cut()
        self.publish(cut)
        with patch.object(automation, "_archive"):
            automation.process_cut(cut.id)
        self.assertIsNone(cut.archived_at)
        with patch.object(reconciliation_service, "compare_frozen", side_effect=AssertionError("Already compared")):
            automation.ingest_due(datetime(2026, 9, 30, 8, 5, tzinfo=automation.CARACAS))
        self.assertIsNotNone(cut.archived_at)
        self.assertEqual(ReconciliationRun.query.count(), 1)

    def test_retry_permissions_and_missing_cut(self):
        url = "/api/reconciliation/cuts/999999/retry"
        self.assertEqual(self.clients["admin"].post(url, json={}).status_code, 404)
        self.assertEqual(self.clients["vendedor"].post(url, json={}).status_code, 403)

    def test_fixed_times_and_filename(self):
        expected = {"0800": "12:00", "1200": "16:00", "1600": "20:00", "1830": "22:30"}
        for slot, utc_clock in expected.items():
            when = automation.scheduled_at(date(2026, 9, 30), slot)
            self.assertEqual(when.strftime("%H:%M"), utc_clock)
            cut = ReconciliationCut(source=automation.SOURCE, scheduled_for=when)
            self.assertEqual(automation.filename_for(cut), f"inventario_2026-09-30_{slot}.csv")
        self.assertEqual(automation.PUNCTUALITY, timedelta(minutes=2))

    def test_manual_comparison_remains_live_and_automatic_uses_frozen_rows(self):
        cut = ReconciliationCut(source=automation.SOURCE,
                                scheduled_for=automation.scheduled_at(date(2026, 9, 30), "0800"),
                                snapshot_status="captured", local_snapshot_at=datetime(2026, 9, 30, 12))
        cut.stock.append(ReconciliationCutStock(product_id=self.product.id, product_code_snapshot="TEST-01", quantity=10))
        db.session.add(cut)
        db.session.commit()
        self.product.current_stock = 13
        db.session.commit()
        automatic = reconciliation_service.parse_csv(b"product_code;quantity\nTEST-01;14\nUNKNOWN;1\n")
        reconciliation_service.compare_frozen(automatic, cut.stock)
        self.assertEqual((automatic[0].system_quantity, automatic[0].difference), (10, 4))
        self.assertEqual(automatic[1].result, "unmatched")
        manual = reconciliation_service.parse_csv(b"product_code;quantity\nTEST-01;14\n")
        reconciliation_service._compare(manual)
        self.assertEqual((manual[0].system_quantity, manual[0].difference), (13, 1))
        self.assertEqual(self.stock(), 13)
        self.assertEqual(StockMovement.query.count(), 0)

    def test_ingest_does_not_synthesize_a_snapshot(self):
        with patch.object(automation, "capture_cut") as capture:
            with patch.object(automation, "scheduled_at", return_value=datetime.utcnow() - timedelta(minutes=30)):
                self.app.config["RECONCILIATION_ACTIVATED_ON"] = datetime.utcnow().date().isoformat()
                automation.ingest_due()
        self.assertTrue(capture.called)
        self.assertEqual(ReconciliationCutStock.query.count(), 0)

    def test_capture_fails_closed_on_sqlite(self):
        with self.assertRaisesRegex(RuntimeError, "MariaDB"):
            automation.capture_cut(date(2026, 9, 30), "0800")
        self.assertEqual(ReconciliationCutStock.query.count(), 0)
        self.assertEqual(self.stock(), 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
