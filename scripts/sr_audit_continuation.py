"""Continue the preserved SR-000038 audit, exclusively in guarded DEMO 5001.

No real credentials or private files are read. Browser accounts are synthetic
fixtures retained with the audit trail. Snapshot artifacts stay container-local.
"""
import json
import sys
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docker_support.guard import validate_demo, validate_demo_url

SNAPSHOT = Path('/tmp/sr-audit-continuation-baseline.json')
UI_LEDGER = Path('/tmp/sr-audit-csrf-ui.json')
PROTECTED = ('products', 'stock_movements', 'purchase_receptions',
             'purchase_reception_lines', 'suppliers', 'product_suppliers')
ACCOUNTS = {
    'asistente': 'AUDIT_SR_ASSISTANT_20261008',
    'admin': 'AUDIT_SR_ADMIN_20261008',
}


def snapshot(connection):
    checksums = connection.execute(text('CHECKSUM TABLE ' + ','.join(PROTECTED))).all()
    assert all(row[1] is not None for row in checksums)
    sr3 = dict(connection.execute(text(
        'SELECT * FROM replenishment_requests WHERE id=3')).mappings().one())
    lines3 = [dict(row) for row in connection.execute(text(
        'SELECT * FROM replenishment_request_lines WHERE request_id=3 ORDER BY id')).mappings()]
    historical = dict(connection.execute(text(
        "SELECT * FROM supplier_quotes WHERE quote_number='CP-000106'")).mappings().one())
    return json.loads(json.dumps({'checksums': dict(checksums), 'sr3': sr3,
                                 'lines3': lines3, 'historical': historical}, default=str))


def status(connection):
    sr = dict(connection.execute(text(
        'SELECT id,request_number,status,origin,created_by_user_id,created_at,'
        'processed_by_user_id,processed_at,cancelled_by_user_id,cancelled_at,purchase_order_id '
        'FROM replenishment_requests WHERE id=38')).mappings().one())
    counts = {table: connection.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar_one()
              for table in ('products', 'stock_movements', 'purchase_orders', 'purchase_receptions',
                            'replenishment_requests', 'supplier_quotes')}
    counts['stock_sum'] = connection.execute(text('SELECT SUM(current_stock) FROM products')).scalar_one()
    orders = [dict(row) for row in connection.execute(text(
        'SELECT id,order_number,status,supplier_id,supplier_quote_id,created_by_user_id,'
        'created_at,approved_by_user_id,approved_at,replenishment_request_id '
        'FROM purchase_orders WHERE replenishment_request_id=38')).mappings()]
    print(json.dumps({'SR38': sr, 'orders': orders, 'counts': counts}, default=str), flush=True)


def seed_accounts(app):
    from app.extensions import db
    from app.models import User
    with app.app_context():
        for role, username in ACCOUNTS.items():
            user = User.query.filter_by(username=username).one_or_none()
            if user is None:
                user = User(name='Auditoria SR ' + role, username=username,
                            email=username + '@example.invalid', role=role, is_active=True)
                user.set_password('SyntheticSrAuditContinuation2026!')
                db.session.add(user)
            else:
                assert user.role == role and user.email == username + '@example.invalid' and user.is_active
        db.session.commit()
        print('Synthetic browser accounts ready; no existing credentials changed.', flush=True)


def integrity(connection):
    queries = {
        'duplicate_sr_orders': 'SELECT COUNT(*) FROM (SELECT replenishment_request_id FROM purchase_orders '
            'WHERE replenishment_request_id IS NOT NULL GROUP BY replenishment_request_id HAVING COUNT(*)>1) x',
        'broken_forward_link': 'SELECT COUNT(*) FROM replenishment_requests s LEFT JOIN purchase_orders o '
            'ON o.id=s.purchase_order_id WHERE s.purchase_order_id IS NOT NULL '
            'AND (o.id IS NULL OR o.replenishment_request_id<>s.id OR o.replenishment_request_id IS NULL)',
        'broken_reverse_link': 'SELECT COUNT(*) FROM purchase_orders o LEFT JOIN replenishment_requests s '
            'ON s.id=o.replenishment_request_id WHERE o.replenishment_request_id IS NOT NULL '
            'AND (s.id IS NULL OR s.purchase_order_id<>o.id OR s.purchase_order_id IS NULL)',
        'orphan_sr_lines': 'SELECT COUNT(*) FROM replenishment_request_lines l LEFT JOIN replenishment_requests s '
            'ON s.id=l.request_id LEFT JOIN products p ON p.id=l.product_id WHERE s.id IS NULL OR p.id IS NULL',
        'invalid_sr_quantity': 'SELECT COUNT(*) FROM replenishment_request_lines WHERE quantity<=0',
        'missing_processing_trace': "SELECT COUNT(*) FROM replenishment_requests WHERE status IN ('processing','fulfilled') "
            'AND (processed_by_user_id IS NULL OR processed_at IS NULL)',
        'missing_cancel_trace': "SELECT COUNT(*) FROM replenishment_requests WHERE status='cancelled' "
            'AND (cancelled_by_user_id IS NULL OR cancelled_at IS NULL)',
    }
    result = {name: connection.execute(text(sql)).scalar_one() for name, sql in queries.items()}
    legacy = [dict(row) for row in connection.execute(text(
        "SELECT id,status,processed_by_user_id,processed_at FROM replenishment_requests "
        "WHERE status IN ('processing','fulfilled') AND (processed_by_user_id IS NULL OR processed_at IS NULL)"
    )).mappings()]
    baseline = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
    expected = {key: baseline['sr3'][key] for key in ('id', 'status', 'processed_by_user_id', 'processed_at')}
    assert legacy == [expected], 'Unexpected missing trace beyond the preserved historical SR-000003.'
    assert all(value == 0 for name, value in result.items() if name != 'missing_processing_trace'), result
    schema = inspect(connection)
    constraints = {table: {'unique': schema.get_unique_constraints(table),
                          'checks': schema.get_check_constraints(table),
                          'foreign_keys': schema.get_foreign_keys(table)}
                   for table in ('replenishment_requests', 'replenishment_request_lines', 'purchase_orders')}
    for table, columns in (('purchase_orders', ['replenishment_request_id']),
                           ('replenishment_requests', ['purchase_order_id']),
                           ('replenishment_requests', ['created_by_user_id', 'idempotency_key']),
                           ('replenishment_request_lines', ['request_id', 'product_id'])):
        assert any(row['column_names'] == columns for row in constraints[table]['unique']), (table, columns)
    print(json.dumps({'integrity': result, 'legacy_trace_failure_preserved': legacy,
                      'new_trace_failures': 0, 'constraints': constraints}, default=str), flush=True)


def http_guards(app, target):
    from app.models import User
    from verify_demo_category_approval import authenticated, expect
    from verify_sr_audit_demo import checksums, no_money
    before = checksums(target)
    with app.app_context():
        assistant_id = User.query.filter_by(username=ACCOUNTS['asistente']).one().id
        admin_id = User.query.filter_by(username=ACCOUNTS['admin']).one().id
        inventory_id = User.query.filter_by(username='AUDIT_SR_BROWSER_20261008').one().id
    cases = []
    csrf_gaps = []
    with authenticated(app, assistant_id) as client:
        path = '/api/replenishment-requests/38'
        row = expect(client, 'GET', path, 200)
        assert row['status'] == 'fulfilled' and row['purchase_order_id'] == 132
        for action in ('start', 'cancel', 'quotes', 'prepare'):
            expect(client, 'POST', path + '/' + action, 409, json={})
            cases.append([action + ' on preserved fulfilled SR38', 409])
        expect(client, 'POST', '/api/purchases/132/duplicate', 409, json={})
        expect(client, 'POST', '/api/purchases', 409, json={'replenishment_request_id': 38,
               'supplier_id': 4, 'supplier_quote_id': 121,
               'items': [{'product_id': item['product_id'], 'quantity': item['quantity']} for item in row['items']]})
        expect(client, 'POST', path + '/start', 403, json={}, headers={'Origin': 'http://example.invalid'})
        expect(client, 'POST', path + '/start?ref=1', 409, json={})
        expect(client, 'POST', path + '/start', 409, json={},
               headers={'Referer': 'http://host.docker.internal:5001/purchases/requests/38?ref=1'})
        expect(client, 'POST', path + '/start', 415, data='text', headers={'Content-Type': 'text/plain'})
        expect(client, 'POST', path + '/start', 400, json={}, headers={'X-CSRFToken': 'invalid'})
        expect(client, 'POST', path + '/start', 400, json={}, headers={'X-CSRFToken': ''})
        expect(client, 'DELETE', path, 405)
        cases.extend([['duplicate linked OC', 409], ['direct second OC', 409], ['foreign Origin', 403],
                      ['reference query/referer', 409], ['non-JSON', 415], ['absent/invalid CSRF', 400], ['unsupported verb', 405]])
        invalid_quote = expect(client, 'POST', '/api/supplier-quotes', 400, json={},
                               headers={'X-CSRFToken': 'invalid'})
        assert invalid_quote.get('error') == 'Token CSRF inválido o ausente.'
        cases.append(['general supplier quote rejects invalid CSRF before business validation', 400])
    with authenticated(app, inventory_id) as client:
        no_money(expect(client, 'GET', '/api/replenishment-requests/38', 200))
        expect(client, 'GET', '/api/supplier-quotes', 405)
        cases.extend([['Inventory SR38 no money', 200], ['supplier quote listing has no GET route', 405]])
    with authenticated(app, admin_id) as client:
        expect(client, 'POST', '/api/purchases/132/approve', 409, json={})
        cases.append(['repeated approval', 409])
        invalid_approval = expect(client, 'POST', '/api/purchases/132/approve', 400, json={},
                                  headers={'X-CSRFToken': 'invalid'})
        assert invalid_approval.get('error') == 'Token CSRF inválido o ausente.'
        cases.append(['general purchase approval rejects invalid CSRF before business validation', 400])
    assert checksums(target) == before, 'Rejected HTTP probes changed operational data.'
    print(json.dumps({'HTTP_guards': cases, 'remaining_CSRF_failures': csrf_gaps,
                      'operational_checksums_unchanged': True}), flush=True)


def cleanup_ui_fixtures(app, target):
    from app.extensions import db
    from verify_sr_audit_demo import Audit, checksums
    ledger = json.loads(UI_LEDGER.read_text(encoding='utf-8'))
    audit = Audit(app)
    audit.created['requests'] = ledger['ids']
    assert len(ledger['ids']) == 2 and not {3, 38}.intersection(ledger['ids'])
    with app.app_context():
        for rid in ledger['ids']:
            state = dict(db.session.execute(text(
                'SELECT notes,status,cancelled_by_user_id,cancelled_at,purchase_order_id '
                'FROM replenishment_requests WHERE id=:id'), {'id': rid}).mappings().one())
            note = state['notes']
            assert note.startswith('Disposable CSRF UI regression 20261008 ')
            if note.endswith('cancel'):
                assert state['status'] == 'cancelled' and state['cancelled_by_user_id'] == 64
                assert state['cancelled_at'] is not None and state['purchase_order_id'] is None
                print(json.dumps({'UI_cancel_trace': {'id': rid, **state}}, default=str), flush=True)
            orders = db.session.execute(text('SELECT id,status FROM purchase_orders WHERE replenishment_request_id=:id'),
                                        {'id': rid}).all()
            assert all(order.status == 'pending_approval' for order in orders)
            for order in orders:
                assert not db.session.execute(text('SELECT id FROM purchase_receptions WHERE purchase_order_id=:id'),
                                              {'id': order.id}).first()
            quotes = db.session.execute(text('SELECT id FROM supplier_quotes WHERE notes=:note'),
                                        {'note': f'Cotización para SR-{rid:06d}'}).scalars().all()
            audit.created['quotes'].extend(quotes)
    audit.cleanup()
    assert checksums(target) == ledger['before'], 'UI fixture cleanup changed original operational data.'
    print('Only exact disposable UI fixture IDs removed; 12 operational checksums unchanged.', flush=True)


def ui_fixtures(app, target):
    from app.models import User, Product
    from verify_demo_category_approval import authenticated, expect
    from verify_sr_audit_demo import checksums
    assert not UI_LEDGER.exists(), 'Existing UI fixture ledger is preserved.'
    before = checksums(target)
    with app.app_context():
        inventory_id = User.query.filter_by(username='AUDIT_SR_BROWSER_20261008').one().id
        product_id = Product.query.filter_by(code='DEMO-003').one().id
    ids = []
    with authenticated(app, inventory_id) as client:
        for kind in ('prepare', 'cancel'):
            row = expect(client, 'POST', '/api/replenishment-requests', 201, json={
                'notes': 'Disposable CSRF UI regression 20261008 ' + kind,
                'items': [{'product_id': product_id, 'quantity': 2}]})
            ids.append(row['id'])
    UI_LEDGER.write_text(json.dumps({'ids': ids, 'before': before}), encoding='utf-8')
    print(json.dumps({'UI_fixture_ids': ids, 'disposable': True}), flush=True)


def main():
    actions = ('baseline', 'accounts', 'status', 'final', 'approval-safety', 'http-guards', 'ui-fixtures', 'ui-cleanup')
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise SystemExit('Usage: sr_audit_continuation.py ' + '|'.join(actions))
    validate_demo()
    from docker_support.bootstrap import engine
    from docker_support.demo import require_ready
    target = engine()
    validate_demo_url(target.url)
    with target.connect() as connection:
        require_ready(connection)
        action = sys.argv[1]
        if action == 'baseline':
            assert not SNAPSHOT.exists(), 'Existing baseline is preserved.'
            SNAPSHOT.write_text(json.dumps(snapshot(connection)), encoding='utf-8')
        if action == 'final':
            assert snapshot(connection) == json.loads(SNAPSHOT.read_text(encoding='utf-8')), 'Protected data changed.'
            integrity(connection)
            print('Protected checksums, SR-000003 and CP-000106 unchanged.', flush=True)
        status(connection)
    if action in ('accounts', 'approval-safety', 'http-guards', 'ui-fixtures', 'ui-cleanup'):
        from app import create_app
        app = create_app()
        validate_demo_url(make_url(app.config['SQLALCHEMY_DATABASE_URI']))
        if action == 'accounts':
            seed_accounts(app)
        elif action == 'http-guards':
            http_guards(app, target)
        elif action == 'ui-fixtures':
            ui_fixtures(app, target)
        elif action == 'ui-cleanup':
            cleanup_ui_fixtures(app, target)
        else:
            from app.services import notification_service
            from app.models.notification import EXTERNAL_CHANNELS
            with app.app_context():
                settings = notification_service._settings()
                channels = {channel: notification_service._channel_on('replenishment', channel, settings)
                            for channel in EXTERNAL_CHANNELS}
                assert not any(channels.values()), 'External notifications must remain inactive.'
                print(json.dumps({'synthetic_demo': True, 'base_url': 'http://localhost:5001',
                                  'external_notification_channels': channels,
                                  'approval_effect': 'local status/actor/time and in-app notification only'}))
    target.dispose()


if __name__ == '__main__':
    main()
