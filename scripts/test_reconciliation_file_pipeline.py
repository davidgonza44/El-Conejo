"""Opt-in full synthetic path; requires the verified private MariaDB launcher."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import time
from datetime import date, datetime, timedelta
from contextlib import contextmanager
from unittest.mock import patch

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flask import Flask
from sqlalchemy import create_engine, text
from app.extensions import db
from app.models import Category, Product, User, Notification, StockMovement
from app.models.reconciliation import ReconciliationRun, ReconciliationCutStock
from app.services import reconciliation_automation_service as auto, reconciliation_service as service
from app.services import rbac_service, notification_providers
from app.utils.schema import reset_cache
from test_reconciliation_automatic_mariadb import _guard, _url
import reconciliation_file_bridge as bridge


@contextmanager
def clock(when):
    class FixtureClock(datetime):
        @classmethod
        def utcnow(cls):
            return when
    with patch.object(auto, 'datetime', FixtureClock):
        yield


def prepare_app(name, inbox, archive):
    app = Flask('synthetic-file-pipeline')
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=_url(name),
        SQLALCHEMY_TRACK_MODIFICATIONS=False, RECONCILIATION_INBOX_PATH=str(inbox),
        RECONCILIATION_ARCHIVE_PATH=str(archive), RECONCILIATION_ACTIVATED_ON='2030-01-01')
    db.init_app(app)
    with app.app_context():
        db.create_all()
        reset_cache()
        rbac_service.ensure_seeded()
        db.session.add_all([Category(id=1,name='Synthetic pipeline'),
            User(id=1,name='Synthetic admin',username='synthetic_admin',email='admin@example.invalid',role='admin'),
            User(id=2,name='Synthetic seller',username='synthetic_seller',email='seller@example.invalid',role='vendedor')])
        db.session.commit()
        with db.engine.begin() as connection:
            for first in range(1,50001,2500):
                connection.execute(Product.__table__.insert(),[dict(id=i,code=f'SYN-BENCH-{i:08d}',
                    name=f'Synthetic {i}',category_id=1,current_stock=10+i%201) for i in range(first,first+2500)])
    return app


class Pipeline:
    def __init__(self, exports, inbox):
        self.exports, self.inbox, self.results = exports, inbox, []
        self.sequence = 0

    def cut(self):
        day = date(2030,1,1) + timedelta(days=self.sequence//4)
        slot = ('0800','1200','1600','1830')[self.sequence%4]
        self.sequence += 1
        with clock(auto.scheduled_at(day,slot)+timedelta(seconds=1)):
            cut = auto.capture_cut(day,slot)
        assert cut.snapshot_status == 'captured'
        assert ReconciliationCutStock.query.filter_by(cut_id=cut.id).count() == 50000
        return cut, day.isoformat(), slot

    def produce(self, cut, day, slot, rows):
        result = bridge.produce(self.exports,day,slot,rows)
        assert Path(result['path']).name == auto.filename_for(cut)
        return Path(result['path'])

    def process(self, cut, delay=300):
        with clock(cut.scheduled_for+timedelta(seconds=delay)):
            result = auto.process_cut(cut.id)
        db.session.expire_all()
        return result

    def notification(self, cut, severity):
        rows = Notification.query.filter_by(entity_type='reconciliation_cut',entity_id=cut.id).all()
        assert len(rows) == 1 and rows[0].user_id == 1 and rows[0].severity == severity
        assert rows[0].href == (f'/reconciliation?detail={cut.run_id}' if cut.run_id else f'/reconciliation?cut={cut.id}')
        return dict(severity=rows[0].severity,message=rows[0].message,href=rows[0].href)

    def record(self, label, cut, **extra):
        self.results.append(dict(case=label,status=cut.status,run_id=cut.run_id,**extra))
        print('PASS', label, flush=True)

    def valid(self, rows):
        cut,day,slot = self.cut()
        path = self.produce(cut,day,slot,rows)
        start = time.perf_counter()
        transferred = bridge.transport(path,self.inbox)
        transfer_s = time.perf_counter()-start
        start = time.perf_counter()
        self.process(cut)
        processing_s = time.perf_counter()-start
        assert cut.status == 'completed' and cut.run.received_count == cut.run.match_count == rows
        assert not (self.inbox/path.name).exists() and cut.archived_at
        archive = auto._root('RECONCILIATION_ARCHIVE_PATH')/'processed'/day/path.name
        assert bridge.digest(path) == bridge.digest(archive)
        note = self.notification(cut,'success')
        self.record(f'valid_{rows}',cut,transport=transferred,transfer_s=transfer_s,processing_s=processing_s,notification=note)
        return cut,path

    def late(self):
        cut,day,slot = self.cut()
        path = self.produce(cut,day,slot,3)
        self.process(cut)
        assert cut.status == 'waiting_source' and cut.run_id is None
        self.notification(cut,'info')
        # Only synthetic stock changes, proving delayed input uses the frozen quantity.
        product = db.session.get(Product,1)
        product.current_stock += 7
        db.session.commit()
        bridge.transport(path,self.inbox)
        self.process(cut,delay=3600)
        assert cut.run.lines[0].system_quantity == 11 and cut.run.match_count == 3
        assert db.session.get(Product,1).current_stock == 18
        assert cut.source_received_at == cut.scheduled_for+timedelta(hours=1)
        note = self.notification(cut,'success')
        db.session.get(Product,1).current_stock = 11
        db.session.commit()
        self.record('late_60_minutes',cut,notification=note,frozen_quantity=11,live_quantity_during_ingestion=18)

    def temporaries(self):
        cut,day,slot = self.cut()
        temporary = self.exports/(auto.filename_for(cut)+'.tmp')
        temporary.write_bytes(b'product_code;quantity\nSYN-BENCH-00000001;')
        try: bridge.transport(temporary,self.inbox)
        except ValueError: pass
        else: raise AssertionError('Transport accepted .tmp')
        (self.inbox/temporary.name).write_bytes(temporary.read_bytes())
        self.process(cut)
        assert cut.status == 'waiting_source' and cut.run_id is None
        self.record('tmp_ignored',cut,notification=self.notification(cut,'info'))

    def partial(self):
        cut,day,slot = self.cut()
        path = self.produce(cut,day,slot,20000)
        observed = []
        def progress(temporary,copied):
            if observed: return
            assert copied < path.stat().st_size and temporary.exists()
            assert not (self.inbox/path.name).exists()
            self.process(cut)
            assert cut.status == 'waiting_source' and cut.run_id is None
            observed.append(copied)
            raise RuntimeError('Synthetic transport interruption')
        try: bridge.transport(path,self.inbox,progress=progress)
        except RuntimeError as exc: assert 'Synthetic' in str(exc)
        else: raise AssertionError('Interruption not injected')
        assert observed and not (self.inbox/path.name).exists()
        bridge.transport(path,self.inbox)
        self.process(cut)
        assert cut.run.received_count == 20000
        self.record('partial_interrupted_then_retry',cut,partial_bytes=observed[0],notification=self.notification(cut,'success'))

    def duplicates(self, cut, path):
        before = ReconciliationRun.query.count()
        first_hash = bridge.digest(path)
        receipt = bridge.transport(path,self.inbox)
        assert receipt['status'] == 'duplicate' and not (self.inbox/path.name).exists()
        self.process(cut)
        assert ReconciliationRun.query.count() == before
        self.record('duplicate_after_archive',cut,notification=self.notification(cut,'success'))
        other = self.exports/'conflict'
        other.mkdir()
        conflicting = other/path.name
        conflicting.write_bytes(b'product_code;quantity\nSYN-BENCH-00000001;99\n')
        try: bridge.transport(conflicting,self.inbox)
        except ValueError: pass
        else: raise AssertionError('Conflicting replay accepted')
        assert bridge.digest(path) == first_hash and ReconciliationRun.query.count() == before
        self.record('conflicting_duplicate_rejected',cut)

    def invalid_inputs(self):
        cut,day,slot = self.cut()
        wrong = self.exports/f'inventario_{day}_0900.csv'
        wrong.write_bytes(b'product_code;quantity\nA;1\n')
        try: bridge.transport(wrong,self.inbox)
        except ValueError: pass
        else: raise AssertionError('Invalid filename accepted')
        (self.inbox/wrong.name).write_bytes(wrong.read_bytes())
        self.process(cut)
        assert cut.status == 'waiting_source' and cut.run_id is None
        self.record('wrong_name_ignored',cut,notification=self.notification(cut,'info'))
        path = self.exports/auto.filename_for(cut)
        stage = path.with_suffix('.csv.tmp')
        stage.write_bytes(b'product_code,quantity\nSYN-BENCH-00000001,11\n')
        bridge.rename_new(stage,path)
        bridge.transport(path,self.inbox)
        self.process(cut)
        assert cut.status == 'failed' and cut.run_id is None
        self.record('invalid_csv',cut,notification=self.notification(cut,'critical'))

    def physical_files(self, manifest):
        evidence = json.loads(Path(manifest).read_text(encoding='utf-8-sig'))
        assert evidence['SyntheticOnly'] is True
        for entry in evidence['Cases']:
            name = bridge.validate_name(entry['Name'])
            source = Path(entry['Destination'])
            assert source.name == name and source.parent.name.startswith('synthetic_transport_')
            assert bridge.digest(source) == {'bytes':entry['Bytes'],'sha256':entry['SHA256']}
            day,slot = name[11:21],name[22:26]
            when = auto.scheduled_at(date.fromisoformat(day),slot)
            with clock(when+timedelta(seconds=1)):
                cut = auto.capture_cut(date.fromisoformat(day),slot)
            bridge.transport(source,self.inbox)
            start = time.perf_counter()
            self.process(cut)
            seconds = time.perf_counter()-start
            assert cut.run.received_count == cut.run.match_count == entry['Rows']
            assert cut.run.sha256 == entry['SHA256']
            self.record(f'physical_C_D_ingestion_{entry["Rows"]}',cut,sha256=cut.run.sha256,
                        processing_s=seconds,notification=self.notification(cut,'success'))

    def run(self, manifest=None):
        assert bridge.MAX_BYTES == service.MAX_BYTES and bridge.MAX_ROWS == service.MAX_ROWS
        before = db.session.execute(text('SELECT COUNT(*),SUM(current_stock),SUM(id*current_stock) FROM products')).one()
        with patch.object(notification_providers,'send',side_effect=AssertionError('External notification forbidden')):
            if manifest:
                self.physical_files(manifest)
            else:
                cut,path = self.valid(3)
                self.duplicates(cut,path)
                self.late()
                self.temporaries()
                self.partial()
                self.invalid_inputs()
                self.valid(20000)
                self.valid(50000)
        after = db.session.execute(text('SELECT COUNT(*),SUM(current_stock),SUM(id*current_stock) FROM products')).one()
        assert before == after and StockMovement.query.count() == 0
        assert Notification.query.filter_by(user_id=2).count() == 0
        return dict(cases=self.results,stock_pre=list(map(int,before)),stock_post=list(map(int,after)),movements=0)


def main(pipeline_type=Pipeline):
    _guard()
    parser = argparse.ArgumentParser()
    parser.add_argument('--exports-root',required=True)
    parser.add_argument('--inbox-root',required=True)
    parser.add_argument('--physical-manifest')
    args = parser.parse_args()
    token = 'synthetic_validation_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+secrets.token_hex(4)
    exports,inbox = Path(args.exports_root).resolve()/token,Path(args.inbox_root).resolve()/token
    assert exports != inbox
    # Exclusively new subdirectories; never enumerate/read pre-existing files in either root.
    exports.mkdir(parents=True,exist_ok=False)
    inbox.mkdir(parents=True,exist_ok=False)
    output = ROOT/'tmp'/token
    output.mkdir(exist_ok=False)
    name = 'reconciliation_test_'+secrets.token_hex(6)
    server = create_engine(_url())
    created = False
    result = dict(exports=str(exports),inbox=str(inbox),database=name,synthetic_only=True)
    try:
        with server.begin() as c:
            assert Path(c.execute(text('SELECT @@datadir')).scalar()).resolve() == Path(os.environ['TEST_MARIADB_DATADIR']).resolve()
            assert c.execute(text('SELECT DATABASE()')).scalar() is None
            c.execute(text('CREATE DATABASE '+name+' CHARACTER SET utf8mb4'))
            created = True
        app = prepare_app(name,inbox,output/'archive')
        with app.app_context():
            try: result.update(pipeline_type(exports,inbox).run(args.physical_manifest))
            finally:
                db.session.remove()
                db.engine.dispose()
    finally:
        if created:
            with server.begin() as c:
                assert name.startswith('reconciliation_test_') and c.execute(text('SELECT DATABASE()')).scalar() is None
                c.execute(text('DROP DATABASE '+name))
            result['database_dropped'] = True
        server.dispose()
        (output/'results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print('RESULT',output/'results.json',flush=True)
    print('PASS: synthetic producer -> transport -> inbox -> reconciliation -> app notification',flush=True)


if __name__ == '__main__':
    main()
