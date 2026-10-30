"""Actual intake/engine/archive on guarded disposable MariaDB; synthetic only."""
from test_reconciliation_file_pipeline import (
    Pipeline, main, db, text, StockMovement, Notification, ReconciliationRun,
    auto, bridge, notification_providers, patch, time,
)
from flask import current_app
from app.models.reconciliation import ReconciliationCut
from app.services import reconciliation_intake_service as intake


class LandingPipeline(Pipeline):
    def completed(self, cut, path, day, rows):
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        start = time.perf_counter()
        self.process(cut)
        elapsed = time.perf_counter() - start
        assert cut.status == 'completed' and cut.run.received_count == cut.run.match_count == rows
        archive = auto._root('RECONCILIATION_ARCHIVE_PATH')/'processed'/day/path.name
        assert bridge.digest(path) == bridge.digest(archive)
        assert not (self.inbox/path.name).exists() and cut.archived_at
        assert before == (path.read_bytes(), path.stat().st_mtime_ns)
        count = ReconciliationRun.query.count()
        self.process(cut)
        assert ReconciliationRun.query.count() == count and not (self.inbox/path.name).exists()
        assert before == (path.read_bytes(), path.stat().st_mtime_ns)
        self.record(f'landing_{rows}_archive_duplicate', cut, seconds=elapsed,
                    sha256=bridge.digest(path)['sha256'], landing_unchanged=True,
                    notification=self.notification(cut,'success'))

    def restart(self):
        cut,day,slot = self.cut()
        path = self.produce(cut,day,slot,3)
        assert intake.prepare(path.name)['status'] == 'ready'
        assert cut.run_id is None
        with patch.object(auto,'_read_file',side_effect=PermissionError('Synthetic transient lock')):
            self.process(cut)
        assert cut.status == 'waiting_source' and cut.run_id is None
        identifier = cut.id
        db.session.remove()
        cut = db.session.get(ReconciliationCut,identifier)
        self.completed(cut,path,day,3)
        self.record('restart_between_copy_and_engine',cut)

    def conflict(self):
        cut,day,slot = self.cut()
        path = self.produce(cut,day,slot,3)
        original = path.read_bytes()
        assert intake.prepare(path.name)['status'] == 'ready'
        # The producer changes its own source; the application must not overwrite either copy.
        path.write_bytes(b'product_code;quantity\nSYN-BENCH-00000001;99\n')
        changed = path.read_bytes()
        self.process(cut)
        assert cut.status == 'waiting_source' and cut.run_id is None
        assert path.read_bytes() == changed and (self.inbox/path.name).read_bytes() == original
        self.record('conflicting_landing_blocks_engine',cut)

    def invalid(self):
        cut,day,_slot = self.cut()
        path = self.exports/auto.filename_for(cut)
        payload = b'product_code,quantity\nSYN-BENCH-00000001,11\n'
        path.write_bytes(payload)
        self.process(cut)
        assert cut.status == 'failed' and cut.run_id is None
        assert path.read_bytes() == payload
        self.record('invalid_csv_existing_engine',cut,notification=self.notification(cut,'critical'))

    def run(self, manifest=None):
        assert manifest is None
        current_app.config.update(RECONCILIATION_LANDING_PATH=str(self.exports),
                                  RECONCILIATION_SETTLE_SECONDS=0)
        query = text('SELECT COUNT(*),SUM(current_stock),SUM(id*current_stock) FROM products')
        before = db.session.execute(query).one()
        with patch.object(notification_providers,'send',side_effect=AssertionError('External send forbidden')):
            for rows in (20000,50000):
                cut,day,slot = self.cut()
                path = self.produce(cut,day,slot,rows)
                self.completed(cut,path,day,rows)
            self.restart()
            self.conflict()
            self.invalid()
        after = db.session.execute(query).one()
        assert before == after and StockMovement.query.count() == 0
        assert Notification.query.filter_by(user_id=2).count() == 0
        assert all(path.suffix == '.csv' for path in self.exports.iterdir())
        return dict(cases=self.results,stock_pre=list(map(int,before)),
                    stock_post=list(map(int,after)),movements=0,landing_has_no_app_artifacts=True)


if __name__ == '__main__':
    main(LandingPipeline)
