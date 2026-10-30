"""SR audit, exclusively guarded synthetic MariaDB DEMO 5001. No dotenv/password output."""
import json
import sys
import uuid
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta
from threading import Barrier, Event
from unittest.mock import patch
from pathlib import Path

from sqlalchemy import inspect, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docker_support.guard import validate_demo, validate_demo_url
from verify_demo_category_approval import authenticated, expect

RESULTS = []
TABLES = ('products', 'stock_movements', 'purchase_orders', 'purchase_order_lines', 'purchase_receptions',
          'purchase_reception_lines', 'replenishment_requests', 'replenishment_request_lines',
          'supplier_quotes', 'supplier_quote_lines', 'suppliers', 'product_suppliers')


def record(name, value):
    RESULTS.append({'case': name, 'result': value})
    print(json.dumps(RESULTS[-1], ensure_ascii=False, default=str), flush=True)


def checksums(target):
    with target.connect() as connection:
        from docker_support.demo import require_ready
        require_ready(connection)
        rows = connection.execute(text('CHECKSUM TABLE ' + ','.join(TABLES))).all()
        assert all(row[1] is not None for row in rows)
        return dict(rows)


def no_money(value):
    if isinstance(value, dict):
        assert not {'cost', 'unit_cost', 'unit_price', 'purchase_price', 'sale_price', 'subtotal',
                    'total', 'pending_amount', 'line_total', 'estimated_amount'}.intersection(value)
        for key, item in value.items():
            if key != 'pagination':
                no_money(item)
    elif isinstance(value, list):
        for item in value:
            no_money(item)


class Audit:
    def __init__(self, app):
        self.app = app
        self.prefix = 'AUDIT-SR-' + uuid.uuid4().hex[:8]
        self.created = {key: [] for key in ('users', 'products', 'suppliers', 'requests', 'quotes')}

    def seed(self):
        from app.extensions import db
        from app.models import Product, User
        from app.models.purchase import Supplier, ProductSupplier
        with self.app.app_context():
            self.users = {role: User.query.filter_by(role=role, is_active=True).first().id
                          for role in ('inventario', 'asistente', 'admin', 'soporte_ti', 'vendedor')}
            for alias, role in (('assistant2', 'asistente'), ('inventory2', 'inventario')):
                row = User(name=self.prefix + alias, username=self.prefix + alias,
                           email=self.prefix + alias + '@example.invalid', role=role)
                db.session.add(row)
                db.session.flush()
                self.users[alias] = row.id
                self.created['users'].append(row.id)
            category = Product.query.filter_by(code='DEMO-003').one().category_id
            self.products = []
            for index in range(2):
                row = Product(code=self.prefix + str(index), name=self.prefix + ' producto ' + str(index),
                    category_id=category, current_stock=17 + index, minimum_stock=0, purchase_price=1,
                    sale_price=2, is_active=True, approval_status='approved')
                db.session.add(row)
                db.session.flush()
                self.products.append(row.id)
                self.created['products'].append(row.id)
            supplier = Supplier(name=self.prefix, tax_id=self.prefix, is_active=True, approval_status='approved')
            db.session.add(supplier)
            db.session.flush()
            self.supplier = supplier.id
            self.created['suppliers'].append(supplier.id)
            for product in self.products:
                db.session.add(ProductSupplier(product_id=product, supplier_id=supplier.id,
                                               purchase_price=1, is_active=True, is_preferred=False))
            db.session.commit()

    def sr(self, client, products=None, **extra):
        data = {'notes': self.prefix, 'items': [{'product_id': key, 'quantity': 2}
                                              for key in (products or self.products[:1])]}
        row = expect(client, 'POST', '/api/replenishment-requests', 201, json={**data, **extra})
        self.created['requests'].append(row['id'])
        return row

    def quote(self, client, sr, **extra):
        from app.services.inventory_service import COMMERCIAL_ZONE
        today = datetime.now(COMMERCIAL_ZONE).date()
        data = {'supplier_id': self.supplier, 'currency': 'VES', 'issued_on': today.isoformat(),
                'valid_until': (today + timedelta(days=5)).isoformat(),
                'items': [{'product_id': item['product_id'], 'unit_price': '3.00'} for item in sr['items']]}
        row = expect(client, 'POST', f"/api/replenishment-requests/{sr['id']}/quotes", 201, json={**data, **extra})
        self.created['quotes'].append(row['id'])
        return row

    def pair(self, operations):
        barrier = Barrier(2)
        def run(operation):
            client, (_, path, data) = operation
            barrier.wait(timeout=15)
            response = client.post('http://host.docker.internal:5001' + path, json=data, timeout=30)
            assert response.status_code != 500, response.text[:100]
            return response.status_code
        # Session creation writes authentication metadata; finish it before racing business actions.
        with ExitStack() as stack:
            clients = [stack.enter_context(authenticated(self.app, self.users[role])) for role, _, _ in operations]
            with ThreadPoolExecutor(max_workers=2) as pool:
                return list(pool.map(run, zip(clients, operations)))

    def rbac(self, clients, sr):
        rid = sr['id']
        for role, client in clients.items():
            permitted = role in ('inventario', 'asistente', 'admin', 'assistant2', 'inventory2')
            codes = []
            for path in ('/purchases/requests', f'/purchases/requests/{rid}',
                         '/api/replenishment-requests', f'/api/replenishment-requests/{rid}'):
                expected = 404 if role == 'inventory2' and path.endswith('/' + str(rid)) else (200 if permitted else 403)
                expect(client, 'GET', path, expected)
                codes.append(expected)
            for action in ('start', 'cancel', 'quotes', 'prepare'):
                if role in ('asistente', 'assistant2'):
                    continue
                expect(client, 'POST', f'/api/replenishment-requests/{rid}/{action}', 403, json={})
            if role != 'inventario' and role != 'inventory2':
                for path in ('/api/replenishment-requests', '/api/predictions/replenishment/orders'):
                    expect(client, 'POST', path, 403, json={'confirm': True, 'items': []})
            if role in ('inventario', 'inventory2'):
                no_money(expect(client, 'GET', '/api/replenishment-requests', 200))
                for path in (f'/api/replenishment-requests/{rid}',):
                    if role == 'inventario':
                        no_money(expect(client, 'GET', path, 200))
            record('RBAC ' + role, codes)
        import requests
        with requests.Session() as anonymous:
            anonymous.trust_env = False
            for path in ('/api/replenishment-requests', f'/api/replenishment-requests/{rid}'):
                expect(anonymous, 'GET', path, 401)
            for action in ('start', 'cancel', 'quotes', 'prepare'):
                expect(anonymous, 'POST', f'/api/replenishment-requests/{rid}/{action}', 401, json={})
            expect(anonymous, 'GET', '/purchases/requests', 302)
        record('RBAC anonymous', 'API 401; page 302 login')

    def edges(self, clients, sr):
        from app.extensions import db
        from app.models import Product
        inv, assistant = clients['inventario'], clients['asistente']
        path = f"/api/replenishment-requests/{sr['id']}"
        for items in ([], [{'product_id': self.products[0], 'quantity': 0}],
                      [{'product_id': self.products[0], 'quantity': -1}],
                      [{'product_id': 2147483647, 'quantity': 1}],
                      [{'product_id': self.products[0], 'quantity': 1}] * 2,
                      [{'product_id': self.products[0], 'quantity': 1}] * 101):
            expect(inv, 'POST', '/api/replenishment-requests', 400, json={'items': items})
        for status, active in (('pending', True), ('rejected', True), ('approved', False)):
            with self.app.app_context():
                product = db.session.get(Product, self.products[1])
                product.approval_status, product.is_active = status, active
                db.session.commit()
            expect(inv, 'POST', '/api/replenishment-requests', 400,
                   json={'items': [{'product_id': self.products[1], 'quantity': 1}]})
        with self.app.app_context():
            product = db.session.get(Product, self.products[1])
            product.approval_status, product.is_active = 'approved', True
            db.session.commit()
        for params in ({'status': 'En proceso'}, {'start': '2026-10-10', 'end': '2026-10-08'},
                       {'page': '0'}, {'per_page': '1000'}, {'end': '9999-12-31'}):
            expect(assistant, 'GET', '/api/replenishment-requests', 400, params=params)
        found = expect(assistant, 'GET', '/api/replenishment-requests', 200,
                       params={'search': self.prefix, 'status': 'open', 'per_page': 8})
        assert sr['id'] in {row['id'] for row in found['items']}
        expect(assistant, 'POST', '/api/replenishment-requests/2147483647/start', 404, json={})
        expect(assistant, 'POST', path + '/prepare', 409, json={'supplier_quote_id': 106})
        expect(assistant, 'POST', path + '/start', 200, json={})
        expect(assistant, 'POST', path + '/start', 409, json={})
        for data in ({'currency': 123}, {'currency': ''}, {'valid_until': '2020-01-01'},
                     {'items': []}, {'items': [{'product_id': self.products[0], 'unit_price': '0'}]},
                     {'items': [{'product_id': self.products[0], 'unit_price': '-1'}]},
                     {'items': [{'product_id': self.products[1], 'unit_price': '1'}]},
                     {'items': [{'product_id': self.products[0], 'unit_price': '1'}] * 2}):
            payload = {'supplier_id': self.supplier, 'currency': 'VES', 'issued_on': '2026-10-08',
                       'valid_until': '2099-01-01', 'items': [{'product_id': self.products[0], 'unit_price': '1'}]}
            expect(assistant, 'POST', path + '/quotes', 400, json={**payload, **data})
        record('HTTP edge cases', 'all invalid inputs 400; repeated/early state 409; nonexistent 404; no 500')

    def surfaces(self, clients):
        from app.extensions import db
        from app.models.purchase import ProductSupplier
        inventory, assistant = clients['inventario'], clients['asistente']
        for role, client in clients.items():
            status = 403 if role in ('vendedor', 'soporte_ti') else 200
            html = expect(client, 'GET', '/predictive/replenishment', status)
            if status == 200:
                assert ('id="rl-order"' in html) == (role in ('inventario', 'inventory2'))
            if role not in ('admin',):
                expect(client, 'POST', '/api/purchases/131/approve', 403, json={})
            if role not in ('asistente', 'assistant2'):
                expect(client, 'POST', '/api/supplier-quotes', 403, json={})
        for _ in range(30):
            response = inventory.get('http://host.docker.internal:5001/api/predictions/export/replenishment', timeout=30)
            if response.status_code != 202:
                break
            assert response.json()['status'] == 'running'
            time.sleep(1)
        assert response.status_code == 200, 'CSV did not finish the truthful 202 forecast state'
        assert not re.search(r'cost|price|subtotal|monto|costo|sale_price|total', response.text.splitlines()[0], re.I)
        for path in ('/purchases/requests/3', '/purchases/requests/36'):
            html = expect(inventory, 'GET', path, 200)
            assert not re.search(r'unit_cost|unit_price|purchase_price|sale_price|data-unit-cost|sr-quote', html)
        legacy = expect(assistant, 'GET', '/purchases/requests/3', 200)
        assert 'No registrado' in legacy and '<option value="106">' not in legacy
        assert '<option value="4">DEMO Suministros Taller 04' in legacy
        expect(assistant, 'POST', '/api/replenishment-requests/3/prepare', 409,
               json={'supplier_quote_id': 106})
        multi = self.sr(inventory, products=self.products)
        path = f"/api/replenishment-requests/{multi['id']}"
        expect(assistant, 'POST', path + '/start', 200, json={})
        with self.app.app_context():
            link = ProductSupplier.query.filter_by(supplier_id=self.supplier, product_id=self.products[1]).one()
            link_id = link.id
            link.is_active = False
            db.session.commit()
        html = expect(assistant, 'GET', f"/purchases/requests/{multi['id']}", 200)
        assert 'No existe un proveedor aprobado y activo asociado a todos los productos de esta solicitud.' in html
        assert '<form id="sr-prepare">' not in html and 'disabled aria-describedby="sr-supplier-notice"' in html
        payload = {'supplier_id': self.supplier, 'currency': 'VES', 'issued_on': '2026-10-08',
                   'valid_until': '2099-01-01', 'items': [{'product_id': key, 'unit_price': 1} for key in self.products]}
        expect(assistant, 'POST', path + '/quotes', 409, json=payload)
        state = expect(assistant, 'GET', path, 200)
        assert state['status'] == 'processing' and not state['purchase_order_id']
        with self.app.app_context():
            db.session.get(ProductSupplier, link_id).is_active = True
            db.session.commit()
        quote = self.quote(assistant, multi)
        expect(assistant, 'POST', path + '/prepare', 200, json={'supplier_quote_id': quote['id']})
        for action in ('start', 'cancel', 'prepare', 'quotes'):
            expect(assistant, 'POST', path + '/' + action, 409, json={})
        no_money(expect(inventory, 'GET', path, 200))
        cancelled = self.sr(inventory)
        cancel_path = f"/api/replenishment-requests/{cancelled['id']}"
        result = expect(assistant, 'POST', cancel_path + '/cancel', 200, json={})
        assert result['cancelled_by'] and result['cancelled_at']
        for action in ('start', 'cancel', 'prepare', 'quotes'):
            expect(assistant, 'POST', cancel_path + '/' + action, 409, json={})
        html = expect(assistant, 'GET', f"/purchases/requests/{cancelled['id']}", 200)
        assert 'Anulada por' in html and 'Fecha de anulación' in html and 'id="sr-actions"' not in html
        record('HTML/CSV/indirect APIs/states', 'RBAC matched; Inventory no prices; legacy CP106 rejected; all closed transitions 409')
        record('Multiproduct', {'SR': multi['id'], 'missing_association': 409,
                               'after_valid_full_quote': 'fulfilled, one OC, original quantities'})

    def races(self, clients):
        inv, assistant = clients['inventario'], clients['asistente']
        for index, kind in enumerate(('start', 'prepare', 'cancel_prepare')):
            sr = self.sr(inv)
            path = f"/api/replenishment-requests/{sr['id']}"
            if kind == 'start':
                operations = [('asistente', path + '/start', {}), ('assistant2', path + '/start', {})]
            else:
                expect(assistant, 'POST', path + '/start', 200, json={})
                quote = self.quote(assistant, sr)
                data = {'supplier_quote_id': quote['id']}
                operations = [('asistente', path + ('/cancel' if kind == 'cancel_prepare' else '/prepare'),
                               {} if kind == 'cancel_prepare' else data), ('assistant2', path + '/prepare', data)]
            codes = self.pair(operations)
            assert sorted(codes) == [200, 409], codes
            row = expect(assistant, 'GET', path, 200)
            from app.extensions import db
            from app.models.purchase import PurchaseOrder
            with self.app.app_context():
                count = PurchaseOrder.query.filter_by(replenishment_request_id=sr['id']).count()
                assert count == (1 if row['status'] == 'fulfilled' else 0)
            record('Race ' + kind, {'HTTP': codes, 'SR': sr['id'], 'state': row['status'], 'linked_OC': count})
        sr = self.sr(inv)
        payload = {'notes': self.prefix + '-retry', 'idempotency_key': uuid.uuid4().hex,
                   'items': [{'product_id': self.products[0], 'quantity': 1}]}
        codes = self.pair([('inventario', '/api/replenishment-requests', payload)] * 2)
        assert codes == [201, 201], codes
        rows = expect(inv, 'GET', '/api/replenishment-requests', 200, params={'search': self.products_code()})['items']
        matches = [row for row in rows if row['notes'] == payload['notes']]
        assert len(matches) == 1
        self.created['requests'].append(matches[0]['id'])
        record('Race create retry', {'HTTP': codes, 'SR': matches[0]['id'], 'count': 1})

    def products_code(self):
        return self.prefix + '0'

    def eligibility_races(self, clients):
        from app.extensions import db
        from app.models import Product, User
        from app.models.purchase import Supplier, PurchaseOrder
        from app.models.supplier_quote import SupplierQuote
        from app.services import purchase_service, replenishment_request_service, supplier_quote_service
        from app.services.exceptions import ApiError
        from app.services.inventory_service import COMMERCIAL_ZONE
        from flask_login import login_user
        today = datetime.now(COMMERCIAL_ZONE).date()
        for kind in ('expiry', 'supplier', 'product'):
            sr = self.sr(clients['inventario'])
            expect(clients['asistente'], 'POST', f"/api/replenishment-requests/{sr['id']}/start", 200, json={})
            quote = self.quote(clients['asistente'], sr)
            event = Event()
            def prepare():
                with self.app.test_request_context():
                    login_user(db.session.get(User, self.users['asistente']))
                    try:
                        replenishment_request_service.prepare_order(sr['id'], {'supplier_quote_id': quote['id']})
                        return 200
                    except ApiError as error:
                        db.session.rollback()
                        return error.status_code
            if kind == 'expiry':
                with self.app.app_context():
                    db.session.get(SupplierQuote, quote['id']).valid_until = today
                    db.session.commit()
                with patch.object(supplier_quote_service, '_today', side_effect=[today, today + timedelta(days=1)]):
                    code = prepare()
            else:
                name = '_supplier' if kind == 'supplier' else '_items'
                original = getattr(purchase_service, name)
                def signal(*args, **kwargs):
                    event.set()
                    return original(*args, **kwargs)
                model, key = (Supplier, self.supplier) if kind == 'supplier' else (Product, self.products[0])
                with self.app.app_context(), patch.object(purchase_service, name, side_effect=signal):
                    locked = model.query.filter_by(id=key).with_for_update().one()
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(prepare)
                        assert event.wait(15), 'submit did not reach locked eligibility resource'
                        if kind == 'supplier':
                            locked.is_active = False
                        else:
                            locked.approval_status = 'rejected'
                        db.session.commit()
                        code = future.result(timeout=30)
                    locked = db.session.get(model, key)
                    locked.is_active = True
                    if kind == 'product':
                        locked.approval_status = 'approved'
                    db.session.commit()
            assert code == 400, (kind, code)
            with self.app.app_context():
                assert PurchaseOrder.query.filter_by(replenishment_request_id=sr['id']).count() == 0
            record('Race eligibility ' + kind, {'status': code, 'SR': sr['id'], 'OC': 0,
                'clock': 'controlled midnight America/Caracas' if kind == 'expiry' else 'real MariaDB row lock'})

    def cleanup(self):
        from app.extensions import db
        from app.models import User
        with self.app.app_context():
            db.session.rollback()
            ids = self.created
            request_ids = list(set(ids['requests']))
            # Only IDs created by this run; both sides of the circular link are cleared first.
            for rid in request_ids:
                orders = db.session.execute(text('SELECT id FROM purchase_orders WHERE replenishment_request_id=:id'), {'id': rid}).scalars().all()
                db.session.execute(text('UPDATE replenishment_requests SET purchase_order_id=NULL WHERE id=:id'), {'id': rid})
                for oid in orders:
                    db.session.execute(text("DELETE FROM notifications WHERE entity_type='purchase_order' AND entity_id=:id"), {'id': oid})
                    db.session.execute(text('DELETE FROM purchase_order_lines WHERE purchase_order_id=:id'), {'id': oid})
                    db.session.execute(text('DELETE FROM purchase_orders WHERE id=:id'), {'id': oid})
                db.session.execute(text('DELETE FROM replenishment_request_lines WHERE request_id=:id'), {'id': rid})
                db.session.execute(text('DELETE FROM replenishment_requests WHERE id=:id'), {'id': rid})
            for qid in ids['quotes']:
                db.session.execute(text('DELETE FROM supplier_quote_lines WHERE quote_id=:id'), {'id': qid})
                db.session.execute(text('DELETE FROM supplier_quotes WHERE id=:id'), {'id': qid})
            for sid in ids['suppliers']:
                db.session.execute(text('DELETE FROM product_suppliers WHERE supplier_id=:id'), {'id': sid})
                db.session.execute(text('DELETE FROM suppliers WHERE id=:id'), {'id': sid})
            for pid in ids['products']:
                db.session.execute(text('DELETE FROM products WHERE id=:id'), {'id': pid})
            for uid in ids['users']:
                db.session.delete(db.session.get(User, uid))
            db.session.commit()


def verify(pending=False):
    validate_demo()
    from docker_support.bootstrap import engine
    from app import create_app
    target = engine()
    before = checksums(target)
    app = create_app()
    audit = Audit(app)
    try:
        audit.seed()
        with ExitStack() as stack:
            clients = {role: stack.enter_context(authenticated(app, key)) for role, key in audit.users.items()}
            if not pending:
                sr = audit.sr(clients['inventario'])
                audit.rbac(clients, sr)
                audit.edges(clients, sr)
            audit.surfaces(clients)
            audit.races(clients)
            audit.eligibility_races(clients)
    finally:
        audit.cleanup()
        after = checksums(target)
        assert before == after, 'Original operational rows changed or fixtures remain'
        target.dispose()
    record('Cleanup', '12 operational table checksums unchanged; SR-000003 and CP-000106 preserved')


def preflight():
    validate_demo()
    from docker_support.bootstrap import engine
    from docker_support.demo import require_ready
    target = engine()
    validate_demo_url(target.url)
    with target.connect() as connection:
        require_ready(connection)
        summary = {}
        for table in ('products', 'stock_movements', 'purchase_orders', 'purchase_receptions',
                      'replenishment_requests', 'supplier_quotes'):
            summary[table] = connection.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar_one()
        summary['stock_sum'] = connection.execute(text('SELECT SUM(current_stock) FROM products')).scalar_one()
        summary['sr3'] = dict(connection.execute(text('SELECT id,request_number,status,origin,created_at,'
            'processed_by_user_id,processed_at,purchase_order_id FROM replenishment_requests WHERE id=3')).mappings().one())
        summary['cp106'] = dict(connection.execute(text('SELECT id,quote_number,supplier_id,status,issued_on,valid_until '
            'FROM supplier_quotes WHERE quote_number=:number'), {'number': 'CP-000106'}).mappings().one())
        summary['constraints'] = {table: {'foreign_keys': inspect(connection).get_foreign_keys(table),
                                         'unique': inspect(connection).get_unique_constraints(table),
                                         'checks': inspect(connection).get_check_constraints(table)}
                                  for table in ('replenishment_requests', 'replenishment_request_lines',
                                                'purchase_orders', 'supplier_quote_lines')}
        print(json.dumps(summary, default=str, ensure_ascii=False))
    target.dispose()


if __name__ == '__main__':
    if sys.argv[1:] == ['--verify']:
        verify()
    elif sys.argv[1:] == ['--pending']:
        verify(pending=True)
    elif not sys.argv[1:]:
        preflight()
    else:
        raise SystemExit('Usage: verify_sr_audit_demo.py [--verify|--pending]')
