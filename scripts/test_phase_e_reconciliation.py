"""Actual MariaDB + real Flask contracts. Synthetic-only guarded server, port 3407."""
import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import create_engine, event, text
from phase_e_fixture import (DAY, CSV, auto, db, build_app, capture, clock, move_after,
                             publish, reconcile, state, assert_integrity, client_for)
from test_reconciliation_automatic_mariadb import _url
from migrate_reconciliation_cuts import validate_target
from app.models import Product, StockMovement
from app.models.reconciliation import ReconciliationCut, ReconciliationRun
from app.models.notification import Notification
from app.services import reconciliation_service as service
from docker_support.scheduler import reconciliation_tick


class PhaseE(unittest.TestCase):
    def setUp(self):
        self.app, self.name = build_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        from flask import g
        def fresh_request():
            g.pop("_login_user", None)
        self.app.before_request_funcs[None].insert(0, fresh_request)

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.ctx.pop()
        validate_target(self.name)
        server = create_engine(_url())
        with server.begin() as connection:
            assert connection.execute(text("SELECT DATABASE()")).scalar() is None
            connection.execute(text(f"DROP DATABASE `{self.name}`"))
        server.dispose()

    def complete(self):
        cut = capture()
        self.assertEqual(cut.status, "waiting_source")
        self.assertIsNone(auto.process_cut(cut.id).run_id)
        move_after()
        publish(cut)
        return reconcile(cut)

    def test_complete_snapshot_csv_audit_permissions_and_idempotency(self):
        cut = self.complete()
        self.assertEqual(cut.status, "completed")
        self.assertEqual((cut.run.match_count, cut.run.discrepancy_count, cut.run.unmatched_count), (1, 2, 1))
        lines = {line.product_code: line for line in cut.run.lines}
        self.assertEqual((lines['SIM-IGUAL'].system_quantity, lines['SIM-IGUAL'].difference), (12, 0))
        self.assertEqual(lines['SIM-FALTA'].difference, -5)
        self.assertEqual(lines['SIM-SOBRA'].difference, 3)
        self.assertEqual(lines['SIM-DESCONOCIDO'].result, 'unmatched')
        self.assertTrue(all(line.review_status == 'pending' and line.stock_movement_id is None for line in cut.run.lines))
        admin = client_for(self.app, 'admin')
        url = f'/api/reconciliation/{cut.run_id}/lines/{lines["SIM-SOBRA"].id}/review'
        response = admin.post(url, json={'note': 'SIMULADO revisar exportación; sin ajuste'})
        self.assertEqual(response.status_code, 200, response.json)
        db.session.expire_all()
        self.assertEqual(lines['SIM-SOBRA'].review_status, 'reviewed')
        self.assertIsNotNone(lines['SIM-SOBRA'].reviewed_by_user_id)
        self.assertIsNotNone(lines['SIM-SOBRA'].reviewed_at)
        self.assertEqual(lines['SIM-FALTA'].review_status, 'pending')
        self.assertEqual(admin.post(url, json={'note':'repetir'}).status_code, 409)
        for role in ('inventario','vendedor','soporte_ti'):
            actor = client_for(self.app, role)
            self.assertEqual(actor.get('/reconciliation').status_code, 403)
            self.assertEqual(actor.get(f'/api/reconciliation/cuts/{cut.id}').status_code, 403)
            self.assertEqual(actor.post(url, json={'note':'denegado'}).status_code, 403)
        before = assert_integrity()
        capture(); move_after(); publish(cut); reconcile(cut)
        self.assertEqual(assert_integrity(), before)
        self.assertEqual(ReconciliationRun.query.count(), 1)
        self.assertGreater(Notification.query.filter_by(entity_type='reconciliation_cut', entity_id=cut.id).count(), 0)

    def test_snapshot_api_is_bounded_validated_and_historical(self):
        cut = capture()
        move_after()
        admin = client_for(self.app, 'admin')
        path = f'/api/reconciliation/cuts/{cut.id}'
        data = admin.get(path).json
        self.assertEqual(data['snapshot']['total'], 3)
        self.assertEqual(data['snapshot']['items'][0]['quantity'], 12)
        self.assertEqual(admin.get(path+'?snapshot_page=0').status_code, 400)
        self.assertEqual(admin.get(path+'?snapshot_page=x').status_code, 400)
        self.assertEqual(admin.get(path+'?snapshot_page=999').json['snapshot']['page'], 1)
        self.assertIn('inventario_2031-01-15_0800.csv', data['expected_filename'])
        self.assertIn('18:30', admin.get('/reconciliation').text)

    def test_capture_failure_persists_and_safe_punctual_retry(self):
        def fail(_conn, _cursor, statement, _params, _context, _many):
            if statement.upper().startswith('INSERT INTO RECONCILIATION_CUT_STOCK'):
                raise RuntimeError('synthetic rollback')
        event.listen(db.engine, 'after_cursor_execute', fail)
        try:
            with self.assertRaisesRegex(RuntimeError, 'synthetic'):
                capture()
        finally:
            event.remove(db.engine, 'after_cursor_execute', fail)
        cut = ReconciliationCut.query.one()
        self.assertEqual((cut.snapshot_status, cut.status, cut.attempt_count), ('pending', 'failed', 1))
        self.assertEqual(len(cut.stock), 0)
        self.assertIsNotNone(cut.last_attempt_at)
        result = capture()
        self.assertEqual(result.id, cut.id)
        self.assertEqual((result.snapshot_status, result.status), ('captured', 'waiting_source'))
        self.assertIsNone(result.failure_reason)
        self.assertEqual(len(result.stock), 3)

    def test_missed_cut_and_restart_do_not_reconstruct(self):
        now = auto.scheduled_at(DAY, '1200') + timedelta(minutes=10)
        with clock(now):
            auto.ingest_due(now.replace(tzinfo=auto.timezone.utc))
        self.assertEqual(ReconciliationCut.query.count(), 2)
        self.assertTrue(all(c.snapshot_status == 'unavailable' for c in ReconciliationCut.query))
        db.session.remove(); db.engine.dispose()
        with clock(now):
            auto.ingest_due(now.replace(tzinfo=auto.timezone.utc))
        self.assertEqual(ReconciliationCut.query.count(), 2)
        self.assertTrue(all(len(c.stock) == 0 for c in ReconciliationCut.query))
        self.assertEqual(StockMovement.query.count(), 3)

    def test_invalid_file_explicit_retry_and_frozen_stock(self):
        cut = capture()
        from pathlib import Path
        path = Path(self.app.instance_path) / 'inbox' / auto.filename_for(cut)
        path.parent.mkdir()
        path.write_bytes(b'bad,columns\nA,2\n')
        with clock(auto.scheduled_at(DAY, '0800') + timedelta(minutes=5)):
            auto.process_cut(cut.id)
        self.assertEqual((cut.status, cut.attempt_count), ('failed', 1))
        move_after()
        path.write_bytes(CSV)  # Explicit corrected private upload, not a changed landing receipt.
        self.assertIsNone(auto.process_cut(cut.id).run_id)
        with clock(auto.scheduled_at(DAY, '0800') + timedelta(minutes=15)):
            auto.retry_failed(cut.id)
        self.assertEqual((cut.status, cut.attempt_count), ('completed', 2))
        self.assertEqual(cut.run.lines[0].system_quantity, 12)
        assert_integrity()

    def test_duplicate_csv_rows_and_unknown_do_not_adjust(self):
        lines = service.parse_csv(b'product_code;quantity\nSIM-IGUAL;12\nsim-igual;99\nUNKNOWN;2\n')
        cut = capture()
        service.compare_frozen(lines, cut.stock)
        self.assertEqual([line.result for line in lines], ['match', 'invalid', 'unmatched'])
        self.assertEqual(Product.query.filter_by(code='SIM-IGUAL').one().current_stock, 12)
        self.assertEqual(StockMovement.query.count(), 3)

    def test_snapshot_pagination_and_code_survive_catalogue_rename(self):
        category = Product.query.first().category_id
        extra = [Product(code=f'SIM-PAGE-{index:03d}', name='SIMULADO paginación',
                         category_id=category, current_stock=0, purchase_price=1, sale_price=2)
                 for index in range(53)]
        db.session.add_all(extra)
        db.session.commit()
        cut = capture()
        extra[-1].code = 'SIM-CODIGO-CAMBIADO'
        db.session.commit()
        admin = client_for(self.app, 'admin')
        path = f'/api/reconciliation/cuts/{cut.id}'
        first = admin.get(path).json['snapshot']
        second = admin.get(path+'?snapshot_page=2').json['snapshot']
        self.assertEqual((first['total'],first['pages'],len(first['items']),len(second['items'])), (56,2,50,6))
        self.assertEqual(second['items'][-1]['product_code'], 'SIM-PAGE-052')
        self.assertEqual(second['items'][-1]['quantity'], 0)

    def test_portable_scheduler_activation_slots_replay_and_restart(self):
        self.app.config['RECONCILIATION_ACTIVATED_ON'] = ''
        with patch.object(auto, 'capture_cut', side_effect=AssertionError('disabled')), patch.object(auto, 'ingest_due', side_effect=AssertionError('disabled')):
            reconciliation_tick()
            self.app.config['RECONCILIATION_ACTIVATED_ON'] = (DAY + timedelta(days=1)).isoformat()
            reconciliation_tick(auto.scheduled_at(DAY,'0800').replace(tzinfo=auto.timezone.utc))
        self.app.config['RECONCILIATION_ACTIVATED_ON'] = DAY.isoformat()
        for slot in auto.TIMES:
            now = auto.scheduled_at(DAY,slot) + timedelta(seconds=5)
            with clock(now):
                reconciliation_tick(now.replace(tzinfo=auto.timezone.utc))
                reconciliation_tick(now.replace(tzinfo=auto.timezone.utc))
            self.app.extensions.pop('reconciliation_last_ingest',None)  # New worker timer.
        self.assertEqual(ReconciliationCut.query.count(),4)
        self.assertTrue(all(c.snapshot_status == 'captured' and c.status == 'waiting_source'
                            and len(c.stock) == 3 for c in ReconciliationCut.query))
        self.assertEqual(StockMovement.query.count(),3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
