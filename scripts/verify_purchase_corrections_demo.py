"""Corrected purchase/SR regressions on guarded DEMO 5001 only.

Baseline contains table checksums only. Creates identified synthetic fixtures,
never receives stock, and removes only exact fixture IDs using the SR verifier.
"""
import json
import sys
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docker_support.guard import validate_demo, validate_demo_url
from verify_demo_category_approval import authenticated, expect
from verify_sr_audit_demo import Audit, checksums, no_money

BASELINE = Path('/tmp/purchase-corrections-checksums.json')


def preserved_readings(app):
    from app.models import User
    with app.app_context():
        assistant = User.query.filter_by(username='AUDIT_SR_ASSISTANT_20261008').one().id
        admin = User.query.filter_by(username='AUDIT_SR_ADMIN_20261008').one().id
        inventory = User.query.filter_by(username='AUDIT_SR_BROWSER_20261008').one().id
    with authenticated(app, assistant) as client:
        order = expect(client, 'GET', '/api/purchases/132', 200)
        assert (order['currency'], order['total'], order['status']) == ('VES', '178.50', 'approved')
        assert order['supplier_quote_id'] == 121 and order['replenishment_request_id'] == 38
        printed = client.get('http://host.docker.internal:5001/purchases/132/print', timeout=30)
        assert printed.status_code == 200 and 'Total: Bs. 178.50' in printed.text and '$ 178.50' not in printed.text
        legacy = expect(client, 'GET', '/api/replenishment-requests/3', 200)
        assert legacy['processed_by'] is None and legacy['processed_at'] is None
        html = client.get('http://host.docker.internal:5001/purchases/requests/3', timeout=30)
        assert '<dt>Procesada por</dt><dd>No registrado</dd>' in html.text
        assert '<dt>Fecha de proceso</dt><dd>No registrado</dd>' in html.text
        for path in ('/api/supplier-quotes', '/api/supplier-quotes/121/cancel', '/api/purchases', '/api/purchases/132/duplicate'):
            for token in ('', 'invalid'):
                response = expect(client, 'POST', path, 400, json={}, headers={'X-CSRFToken': token})
                assert response['error'] == 'Token CSRF inválido o ausente.'
        response = expect(client, 'PATCH', '/api/purchases/132', 400, json={}, headers={'X-CSRFToken': ''})
        assert response['error'] == 'Token CSRF inválido o ausente.'
        expect(client, 'PATCH', '/api/purchases/132', 409, json={'notes': 'must remain immutable'})
        for method in ('PUT', 'DELETE'):
            expect(client, method, '/api/purchases/132', 405, json={})
    with authenticated(app, admin) as client:
        for action in ('approve', 'reject', 'cancel', 'closure-requests/approve', 'closure-requests/reject'):
            response = expect(client, 'POST', '/api/purchases/132/' + action, 400, json={}, headers={'X-CSRFToken': 'invalid'})
            assert response['error'] == 'Token CSRF inválido o ausente.'
        expect(client, 'POST', '/api/purchases/132/approve', 409, json={})
    with authenticated(app, inventory) as client:
        no_money(expect(client, 'GET', '/api/replenishment-requests/38', 200))
        order = expect(client, 'GET', '/api/purchases/132', 200)
        no_money(order)
        response = expect(client, 'POST', '/api/purchases/132/receptions', 400, json={}, headers={'X-CSRFToken': ''})
        assert response['error'] == 'Token CSRF inválido o ausente.'
        printed = client.get('http://host.docker.internal:5001/purchases/132/print', timeout=30)
        assert printed.status_code == 200 and '178.50' not in printed.text and 'Costo unitario' not in printed.text
    print('PASS: preserved OC132=VES/Bs.178.50, SR3 NULL/No registrado, GET/RBAC, all HTTP verbs and CSRF probes.', flush=True)


def cycles(app):
    from app.services.inventory_service import COMMERCIAL_ZONE
    audit = Audit(app)
    try:
        audit.seed()
        with ExitStack() as stack:
            clients = {role: stack.enter_context(authenticated(app, key)) for role, key in audit.users.items()}
            today = datetime.now(COMMERCIAL_ZONE).date()
            for currency, symbol in (('USD', '$'), ('VES', 'Bs.')):
                sr = audit.sr(clients['inventario'])
                path = f"/api/replenishment-requests/{sr['id']}"
                started = expect(clients['asistente'], 'POST', path + '/start', 200, json={})
                assert started['processed_by']['id'] == audit.users['asistente'] and started['processed_at']
                data = {'supplier_id': audit.supplier, 'currency': currency, 'issued_on': today.isoformat(),
                        'valid_until': (today + timedelta(days=3)).isoformat(), 'notes': audit.prefix,
                        'items': [{'product_id': audit.products[0], 'quantity': 2, 'unit_price': '8.50'}]}
                expect(clients['asistente'], 'POST', '/api/supplier-quotes', 400, json={**data, 'currency': 'EUR'})
                quote = expect(clients['asistente'], 'POST', '/api/supplier-quotes', 201, json=data)
                audit.created['quotes'].append(quote['id'])
                payload = {'supplier_id': audit.supplier, 'supplier_quote_id': quote['id'],
                           'replenishment_request_id': sr['id'], 'items': [{'product_id': audit.products[0], 'quantity': 2}]}
                expect(clients['asistente'], 'POST', '/api/purchases', 400, json={**payload, 'currency': 'EUR'})
                # General purchase creation and SR preparation exercise both paths.
                if currency == 'USD':
                    order = expect(clients['asistente'], 'POST', '/api/purchases', 201, json=payload)
                else:
                    prepared = expect(clients['asistente'], 'POST', path + '/prepare', 200, json={'supplier_quote_id': quote['id']})
                    order = expect(clients['asistente'], 'GET', '/api/purchases/' + str(prepared['purchase_order_id']), 200)
                assert order['currency'] == currency and order['total'] == '17.00'
                approved = expect(clients['admin'], 'POST', f"/api/purchases/{order['id']}/approve", 200, json={})
                assert approved['currency'] == currency and approved['status'] == 'approved'
                finished = expect(clients['asistente'], 'GET', path, 200)
                assert finished['processed_at'] == started['processed_at'] and finished['status'] == 'fulfilled'
                no_money(expect(clients['inventario'], 'GET', path, 200))
                html = clients['asistente'].get(f"http://host.docker.internal:5001/purchases/{order['id']}/print", timeout=30)
                assert html.status_code == 200 and 'Total: ' + symbol + ' 17.00' in html.text
                expect(clients['asistente'], 'POST', path + '/prepare', 409, json={'supplier_quote_id': quote['id']})
                print(json.dumps({'cycle': currency, 'SR': sr['id'], 'CP': quote['id'], 'OC': order['id'],
                                  'approved': True, 'processor_recorded_at_start': True, 'no_stock_receipt': True}), flush=True)
    finally:
        # Synthetic fixture IDs only, never SR3/SR38/CP121/OC132.
        assert not {3, 38}.intersection(audit.created['requests'])
        audit.cleanup()


def main():
    assert sys.argv[1:] in (['baseline'], ['verify'])
    validate_demo()
    from docker_support.bootstrap import engine
    from docker_support.demo import require_ready
    target = engine()
    validate_demo_url(target.url)
    with target.connect() as connection:
        require_ready(connection)
        old = connection.execute(text('SELECT created_at,processed_by_user_id,processed_at '
                                      'FROM replenishment_requests WHERE id=3')).mappings().one()
        assert old['processed_by_user_id'] is None and old['processed_at'] is None
        print(json.dumps({'historical_SR3': dict(old)}, default=str), flush=True)
    before = checksums(target)
    if sys.argv[1] == 'baseline':
        assert not BASELINE.exists(), 'Existing baseline retained.'
        BASELINE.write_text(json.dumps(before), encoding='utf-8')
        print('Baseline: 12 operational table checksums recorded, no row data copied.', flush=True)
    else:
        assert before == json.loads(BASELINE.read_text(encoding='utf-8'))
        from app import create_app
        app = create_app()
        validate_demo_url(make_url(app.config['SQLALCHEMY_DATABASE_URI']))
        preserved_readings(app)
        cycles(app)
        assert checksums(target) == before, 'Original data changed or fixtures remain.'
        print('PASS: all 12 operational checksums identical; SR3/SR38/CP121/OC132 and stock/movements preserved.', flush=True)
    target.dispose()


if __name__ == '__main__':
    main()
