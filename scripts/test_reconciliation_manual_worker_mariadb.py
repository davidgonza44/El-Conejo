"""Option A acceptance. Runs only on an explicitly isolated restored MariaDB.

All 2032 dates, CSVs and mutations are synthetic and confined to that clone.
No listening host port, production clock change or DEMO write is involved.
"""
import hashlib
import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import event, text
from test_reconciliation_cut_web_mariadb import APP, client, token, upload
from app.extensions import db
from app.models import Product, StockMovement
from app.models.reconciliation import ReconciliationCut, ReconciliationCutStock
from app.services import reconciliation_automation_service as auto
from docker_support import scheduler


DAY = date(2032, 1, 15)


class Clock(datetime):
    instant = datetime(2032, 1, 15, 12, tzinfo=timezone.utc)

    @classmethod
    def utcnow(cls):
        return cls.instant.astimezone(timezone.utc).replace(tzinfo=None)

    @classmethod
    def now(cls, tz=None):
        return cls.instant.astimezone(tz) if tz else cls.utcnow()


def at(day, slot, seconds=0):
    return auto.scheduled_at(day, slot).replace(tzinfo=timezone.utc) + timedelta(seconds=seconds)


def tick(instant):
    Clock.instant = instant
    with patch.object(auto, 'datetime', Clock), patch.object(auto, 'ingest_due', side_effect=AssertionError('Automatic ingestion prohibited')):
        scheduler.reconciliation_tick(instant)


def digest_stock():
    return {p.id: (p.code, p.current_stock) for p in Product.query.order_by(Product.id)}


class ManualWorkerAcceptance(unittest.TestCase):
    def setUp(self):
        self.context = APP.app_context(); self.context.push()
        self.addCleanup(self.context.pop)
        self.config = patch.dict(APP.config, RECONCILIATION_ACTIVATED_ON=DAY.isoformat(),
                                 RECONCILIATION_ACTIVATED_AT=at(DAY, '0800').isoformat(),
                                 RECONCILIATION_AUTO_INGEST_ENABLED=False)
        self.config.start(); self.addCleanup(self.config.stop)
        self.stock = digest_stock(); self.moves = StockMovement.query.count()
        self.addCleanup(self.verify_preservation)

    def verify_preservation(self):
        db.session.remove()
        self.assertEqual(digest_stock(), self.stock)
        self.assertEqual(StockMovement.query.count(), self.moves)

    def test_01_four_real_worker_slots_no_csv_pending_restart_and_duplicates(self):
        total = ReconciliationCut.query.count()
        tick(at(DAY, '0800', -1))
        self.assertEqual(ReconciliationCut.query.count(), total)
        for slot in auto.TIMES:
            tick(at(DAY, slot, 5))
            cut = ReconciliationCut.query.filter_by(source=auto.SOURCE, scheduled_for=auto.scheduled_at(DAY, slot)).one()
            self.assertEqual((cut.status, cut.snapshot_status, cut.run_id), ('waiting_source', 'captured', None))
            self.assertEqual(cut.local_snapshot_at, at(DAY,slot,5).replace(tzinfo=None))
            frozen = [(s.product_id,s.product_code_snapshot,s.quantity) for s in cut.stock]
            self.assertEqual({s.product_id:(s.product_code_snapshot,s.quantity) for s in cut.stock},self.stock)
            tick(at(DAY,slot,25)); db.session.remove(); db.engine.dispose()
            tick(at(DAY,slot,45))
            again=db.session.get(ReconciliationCut,cut.id)
            self.assertEqual([(s.product_id,s.product_code_snapshot,s.quantity) for s in again.stock],frozen)
        self.assertEqual(ReconciliationCut.query.count(),total+4)
        # A new process with the same durable DB is a genuine worker restart.
        code="""
from datetime import datetime
from app import create_app
from docker_support.scheduler import reconciliation_tick
a=create_app()
with a.app_context(): reconciliation_tick(datetime.fromisoformat('2032-01-15T23:00:00+00:00'))
"""
        values={**os.environ,'RECONCILIATION_ACTIVATED_ON':DAY.isoformat(),
                'RECONCILIATION_ACTIVATED_AT':at(DAY,'0800').isoformat(),'RECONCILIATION_AUTO_INGEST_ENABLED':'false'}
        result=subprocess.run([sys.executable,'-B','-c',code],env=values,capture_output=True)
        self.assertEqual(result.returncode,0)
        db.session.remove(); self.assertEqual(ReconciliationCut.query.count(),total+4)

    def test_02_late_failure_expiry_no_historical_discovery_and_rollback(self):
        day=DAY+timedelta(days=1); total=ReconciliationCut.query.count()
        tick(at(day,'0800',120))
        self.assertEqual(ReconciliationCut.query.count(),total)
        def fail(c,cur,sql,*args):
            if sql.upper().startswith('INSERT INTO RECONCILIATION_CUT_STOCK'):
                raise RuntimeError('SYNTHETIC rollback in isolated test')
        event.listen(db.engine,'after_cursor_execute',fail)
        try:
            with self.assertRaises(RuntimeError): tick(at(day,'1200',5))
        finally: event.remove(db.engine,'after_cursor_execute',fail)
        failed=ReconciliationCut.query.filter_by(source=auto.SOURCE,scheduled_for=auto.scheduled_at(day,'1200')).one()
        self.assertEqual(failed.snapshot_status,'pending'); self.assertFalse(failed.stock)
        failed_id=failed.id
        tick(at(day,'1200',120)); db.session.remove()
        failed=db.session.get(ReconciliationCut,failed_id)
        self.assertEqual((failed.snapshot_status,failed.status),('unavailable','failed'))
        self.assertIsNone(failed.local_snapshot_at); self.assertFalse(failed.stock)
        tick(at(day+timedelta(days=5),'1830',121))
        self.assertEqual(ReconciliationCut.query.count(),total+1)

    def test_03_capture_concurrency_and_punctual_recovery(self):
        instant=at(DAY+timedelta(days=2),'0800',5); Clock.instant=instant
        def capture(_):
            with APP.app_context(): return auto.capture_cut(instant.astimezone(auto.CARACAS).date(),'0800').id
        with patch.object(auto,'datetime',Clock), ThreadPoolExecutor(max_workers=4) as pool:
            ids=list(pool.map(capture,range(4)))
        self.assertEqual(len(set(ids)),1)
        db.session.remove()
        self.assertEqual(ReconciliationCutStock.query.filter_by(cut_id=ids[0]).count(),len(self.stock))
        self.assertEqual({s.product_id:(s.product_code_snapshot,s.quantity) for s in db.session.get(ReconciliationCut,ids[0]).stock},self.stock)

    def test_04_manual_csv_temporal_rejection_original_snapshot_and_differences(self):
        cut=ReconciliationCut.query.filter_by(source=auto.SOURCE,scheduled_for=auto.scheduled_at(DAY,'0800')).one()
        cut_id=cut.id
        frozen={s.product_code_snapshot:s.quantity for s in cut.stock}
        positive=next(code for code,qty in frozen.items() if qty>0)
        other=next(code for code in frozen if code!=positive)
        content=('product_code;quantity\n'+''.join(f'{code};{qty + (2 if code==other else -1 if code==positive else 0)}\n'
                 for code,qty in frozen.items())+'DEMO-ISOLATED-UNKNOWN;3\n').encode()
        filename=auto.filename_for(cut)
        # Real HTTP requests have distinct Flask g; do not reuse an outer ORM context.
        self.context.pop()
        try:
            actor=client(); headers=token(actor)
            for wrong in ('DEMO.csv',filename.replace('_0800','_1200'),'inventario_2032-01-14_0800.csv'):
                self.assertEqual(upload(actor,headers,cut_id,content,wrong).status_code,400)
            self.assertIsNone(actor.get(f'/api/reconciliation/cuts/{cut_id}').json['run_id'])
            response=upload(actor,headers,cut_id,content,filename)
            self.assertEqual(response.status_code,201,response.json)
            run=response.json
            self.assertEqual((run['discrepancy_count'],run['unmatched_count']),(2,1))
            self.assertEqual(sorted(item['difference'] for item in run['items'] if item['result']=='discrepancy'),[-1,2])
            response=actor.post(f"/api/reconciliation/{run['id']}/confirm",json={},headers=headers)
            self.assertEqual(response.status_code,200)
            self.assertEqual(upload(actor,headers,cut_id,content,filename).json['id'],run['id'])
        finally:
            self.context.push()
        db.session.remove()
        self.assertEqual({s.product_code_snapshot:s.quantity for s in db.session.get(ReconciliationCut,cut_id).stock},frozen)

    def test_05_daily_0005_stays_separate(self):
        from app.models import InventorySnapshot
        from app.services import inventory_snapshot_service as snapshots
        day=DAY+timedelta(days=3)
        instant=datetime.combine(day,datetime.min.time(),auto.CARACAS)+timedelta(minutes=5,seconds=5)
        recon=ReconciliationCut.query.count()
        with patch.dict(APP.config,INVENTORY_SNAPSHOT_ACTIVATED_AT=(instant-timedelta(hours=1)).isoformat()):
            result=scheduler.daily_snapshot_tick(instant)
            self.assertFalse(result['skipped'])
            rows=InventorySnapshot.query.filter_by(snapshot_date=day-timedelta(days=1)).all()
            self.assertEqual({s.product_id:s.stock for s in rows},{k:v[1] for k,v in self.stock.items()})
            self.assertTrue(scheduler.daily_snapshot_tick(instant+timedelta(seconds=20))['skipped'])
        self.assertEqual(ReconciliationCut.query.count(),recon)


if __name__=='__main__':
    unittest.main(verbosity=2)
