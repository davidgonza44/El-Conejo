"""GET/SQL currency audit exclusively on guarded DEMO 5001; no business writes.

Run in the validated DEMO app container: baseline, then verify.
Stores only checksums/counts and a history hash in /tmp; no credentials or rows.
Temporary authentication sessions are removed by their own IDs in finally.
"""
import csv
import hashlib
import json
import secrets
import sys
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path

import requests
from flask import session
from flask_login import login_user
from sqlalchemy import text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docker_support.guard import validate_demo, validate_demo_url

BASE = 'http://host.docker.internal:5001'
BASELINE = Path('/tmp/suppliers-currency-integrity.json')
TABLES = ('products', 'stock_movements', 'purchase_orders', 'purchase_order_lines', 'purchase_receptions',
          'purchase_reception_lines', 'replenishment_requests', 'replenishment_request_lines',
          'supplier_quotes', 'supplier_quote_lines', 'suppliers', 'product_suppliers')


def snapshot(target):
    from docker_support.demo import require_ready
    with target.connect() as connection:
        require_ready(connection)
        checksums = connection.execute(text('CHECKSUM TABLE ' + ','.join(TABLES))).all()
        assert all(checksum is not None for _, checksum in checksums)
        rows = connection.execute(text('SELECT po.*, sq.currency AS quote_currency FROM purchase_orders po '
                                       'LEFT JOIN supplier_quotes sq ON sq.id=po.supplier_quote_id ORDER BY po.id')).all()
        unknown = connection.execute(text('SELECT COUNT(*) FROM purchase_orders po LEFT JOIN supplier_quotes sq '
                                          'ON sq.id=po.supplier_quote_id WHERE sq.currency IS NULL')).scalar_one()
        return {'checksums': {name.rsplit('.', 1)[-1]: value for name, value in checksums},
                'orders': len(rows), 'unknown_orders': unknown,
                'history_hash': hashlib.sha256(json.dumps([tuple(r) for r in rows], default=str).encode()).hexdigest()}


@contextmanager
def authenticated(app, user_id):
    from app.extensions import db
    from app.models import User, UserSession
    token = secrets.token_urlsafe(32)
    with app.test_request_context(base_url=BASE):
        record = UserSession(user_id=user_id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                             expires_at=datetime.utcnow() + timedelta(minutes=10))
        db.session.add(record)
        db.session.commit()
        key = record.id
        login_user(db.session.get(User, user_id))
        session['sid'] = token
        session['auth_at'] = datetime.utcnow().timestamp()
        cookie = app.session_interface.get_signing_serializer(app).dumps(dict(session))
    try:
        with requests.Session() as client:
            client.trust_env = False
            client.cookies.set(app.config['SESSION_COOKIE_NAME'], cookie)
            yield client
    finally:
        with app.app_context():
            record = db.session.get(UserSession, key)
            if record:
                db.session.delete(record)
                db.session.commit()


def get(client, path, params=None, status=200):
    response = client.get(BASE + path, params=params, timeout=30, allow_redirects=False)
    assert response.status_code == status, (path, response.status_code)
    return response


def all_items(client, **params):
    data = get(client, '/api/suppliers', {**params, 'per_page': 8}).json()
    rows = list(data['items'])
    for page in range(2, data['pagination']['pages'] + 1):
        part = get(client, '/api/suppliers', {**params, 'per_page': 8, 'page': page}).json()
        assert part['pagination']['total'] == data['pagination']['total']
        rows.extend(part['items'])
    assert len(rows) == len({r['id'] for r in rows}) == data['pagination']['total']
    return data, rows


def expected(target):
    amounts, unknown = defaultdict(dict), defaultdict(dict)
    with target.connect() as connection:
        orders = connection.execute(text('SELECT po.id, po.supplier_id, po.total, po.status, sq.currency '
            'FROM purchase_orders po LEFT JOIN supplier_quotes sq ON sq.id=po.supplier_quote_id')).all()
    for key, supplier, amount, status, currency in orders:
        if currency is None:
            unknown[supplier][key] = f'{amount:.2f}'
        if currency in ('USD', 'VES') and status not in ('cancelled', 'rejected'):
            amounts[supplier][currency] = amounts[supplier].get(currency, Decimal(0)) + amount
    return amounts, unknown


def check_supplier(client, row, amounts, unknown):
    sid = row['id']
    totals = [{'currency': currency, 'amount': f'{amount:.2f}'} for currency, amount in sorted(amounts[sid].items())]
    assert row['totals_by_currency'] == totals and 'total' not in row
    assert row['unknown_currency_orders'] == len(unknown[sid])
    detail = get(client, '/api/suppliers/' + str(sid)).json()
    assert detail['totals_by_currency'] == totals
    assert {o['id']: o['total'] for o in detail['unknown_currency_order_details']} == unknown[sid]
    assert all(o['currency'] is None for o in detail['unknown_currency_order_details'])


def verify_currency(client, currency, rows, amounts):
    for sort in ('total_asc', 'total_desc'):
        _, ordered = all_items(client, currency=currency, sort=sort)
        present = [r for r in rows if currency in amounts[r['id']]]
        present.sort(key=lambda r: ((-1 if sort == 'total_desc' else 1) * amounts[r['id']][currency], r['id']))
        absent = sorted((r for r in rows if currency not in amounts[r['id']]), key=lambda r: r['id'])
        assert [r['id'] for r in ordered] == [r['id'] for r in present + absent]
        assert get(client, '/api/suppliers', {'currency': currency, 'sort': sort, 'per_page': 8}).json()['items'] == ordered[:8]
    params = {'currency': currency, 'min_total': '1', 'sort': 'total_desc'}
    data, filtered = all_items(client, **params)
    ids = {r['id'] for r in rows if amounts[r['id']].get(currency, 0) >= 1}
    assert {r['id'] for r in filtered} == ids
    exported = get(client, '/api/suppliers/export', {**params, 'format': 'csv', 'page': 2})
    records = list(csv.DictReader(StringIO(exported.content.decode('utf-8-sig'))))
    assert {r['RIF'] for r in records} == {r['tax_id'] for r in filtered}
    # An upper bound is evaluated in the same explicit denomination.
    _, bounded = all_items(client, currency=currency, min_total='1', max_total='100', sort='name_asc')
    assert {r['id'] for r in bounded} == {r['id'] for r in rows if 1 <= amounts[r['id']].get(currency, 0) <= 100}
    assert get(client, '/suppliers', params).status_code == 200
    print(f'PASS HTTP 5001: {currency}, both sort directions, stable pages, min/max, count={data["pagination"]["total"]}, CSV.', flush=True)


def verify(app, target, baseline):
    from app.models import User
    from app.services.inventory_service import COMMERCIAL_ZONE, _utc_boundary
    with app.app_context():
        users = {role: User.query.filter_by(role=role, is_active=True).first().id
                 for role in ('asistente', 'admin', 'inventario', 'vendedor', 'soporte_ti')}
    amounts, unknown = expected(target)
    with authenticated(app, users['asistente']) as client:
        data, rows = all_items(client)
        assert data['kpis']['unknown_currency_orders'] == baseline['unknown_orders'] == 22
        assert data['kpis']['orders'] == baseline['orders'] and 'total' not in data['kpis']
        for row in rows:
            check_supplier(client, row, amounts, unknown)
        today = datetime.now(COMMERCIAL_ZONE).date()
        with target.connect() as connection:
            monthly = connection.execute(text('SELECT sq.currency, SUM(po.total) FROM purchase_orders po '
                'JOIN supplier_quotes sq ON sq.id=po.supplier_quote_id WHERE sq.currency IN (\'USD\',\'VES\') '
                'AND po.status NOT IN (\'cancelled\',\'rejected\') AND po.ordered_at >= :start '
                'AND po.ordered_at < :end GROUP BY sq.currency ORDER BY sq.currency'),
                {'start': _utc_boundary(today.replace(day=1)), 'end': _utc_boundary(today + timedelta(days=1))}).all()
        assert data['kpis']['totals_by_currency'] == [{'currency': c, 'amount': f'{a:.2f}'} for c, a in monthly]
        for currency in ('USD', 'VES'):
            verify_currency(client, currency, rows, amounts)
        for path in ('/suppliers', '/api/suppliers', '/api/suppliers/export'):
            for params in ({'min_total': '20'}, {'max_total': '20'}, {'sort': 'total_desc'}, {'sort': 'total_asc'}):
                get(client, path, params, status=400)
        for sort in ('name_asc', 'name_desc', 'last_asc', 'last_desc'):
            all_items(client, sort=sort)
        html = get(client, '/suppliers').text
        assert '22 órdenes históricas sin moneda' in html
        assert 'Moneda no registrada ·' not in html and 'USD ($)' in html and 'VES (Bs.)' in html
        for fmt in ('csv', 'xlsx', 'pdf'):
            get(client, '/api/suppliers/export', {'format': fmt})
        print(f'PASS: {len(rows)} suppliers, monthly sums, every historical order individually identified, all export formats.', flush=True)
    with authenticated(app, users['admin']) as client:
        get(client, '/suppliers')
        get(client, '/api/suppliers')
    for role in ('inventario', 'vendedor', 'soporte_ti'):
        with authenticated(app, users[role]) as client:
            for path in ('/suppliers', '/api/suppliers', '/api/suppliers/export', '/api/suppliers/' + str(rows[0]['id'])):
                get(client, path, status=403)
            if role == 'inventario':
                # The existing physical purchase API remains available without cost.
                with target.connect() as connection:
                    key = connection.execute(text('SELECT MAX(id) FROM purchase_orders')).scalar_one()
                order = get(client, '/api/purchases/' + str(key)).json()
                assert not {'total', 'subtotal', 'pending_amount'}.intersection(order)
                assert all('unit_cost' not in line and 'line_total' not in line for line in order['items'])
    print('PASS HTTP 5001: Admin/Assistant read; Inventory/Seller/IT denied; Inventory purchase API has no amounts.', flush=True)


def main():
    assert sys.argv[1:] in (['baseline'], ['verify'])
    validate_demo()
    from docker_support.bootstrap import engine
    target = engine()
    validate_demo_url(target.url)
    before = snapshot(target)
    assert before['unknown_orders'] == 22
    if sys.argv[1] == 'baseline':
        assert not BASELINE.exists(), 'Never overwrite a previous baseline.'
        BASELINE.write_text(json.dumps(before), encoding='utf-8')
        print(f'Baseline DEMO 5001: {before["orders"]} orders, 22 unknown; 12 checksums + complete order/quote-currency hash.', flush=True)
    else:
        baseline = json.loads(BASELINE.read_text(encoding='utf-8'))
        assert before == baseline, 'Data changed before HTTP verification.'
        from app import create_app
        app = create_app()
        validate_demo_url(make_url(app.config['SQLALCHEMY_DATABASE_URI']))
        verify(app, target, baseline)
        assert snapshot(target) == baseline, 'Historical or operational data changed.'
        print('PASS: 12 table checksums and full history hash identical; all 22 unknown orders, original links, totals and statuses preserved.', flush=True)
    target.dispose()


if __name__ == '__main__':
    main()
