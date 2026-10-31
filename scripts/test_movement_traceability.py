"""Traceability API regressions. In-memory SQLite; MySQL connections explicitly denied."""
import unittest
import uuid
import sys
from datetime import datetime
from io import BytesIO

from flask_login import login_user
from sqlalchemy import event, select

from approval_test_support import IsolatedCase, accredit_run
from app.extensions import db
from app.models import Customer, Permission, Role, SaleLine, StockMovement
from app.models.reconciliation import ReconciliationLine, ReconciliationRun
from app.services import inventory_service, movement_export_service, rbac_service
from app.services.movement_traceability_service import SERVER_FIELDS, creation_fields
from app.services.exceptions import ValidationError


class TraceabilityTests(IsolatedCase):
    def manual(self, **fields):
        return self.clients['admin'].post('/api/inventory/entry', json={
            'product_id': self.product.id, 'quantity': 1, **fields})

    def detail(self, movement_id, actor='admin'):
        response = self.clients[actor].get(f'/api/inventory/movements/{movement_id}')
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def sale(self, actor='admin'):
        customer = Customer(name='Cliente sintético')
        db.session.add(customer)
        db.session.commit()
        response = self.clients[actor].post('/api/sales', json={
            'customer_id': customer.id, 'payment_method': 'cash', 'payment_condition': 'cash',
            'idempotency_key': uuid.uuid4().hex,
            'items': [{'product_id': self.product.id, 'quantity': 1}]})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def purchase(self):
        response = self.clients['inventario'].post('/api/purchases', json={
            'supplier_id': self.supplier.id,
            'items': [{'product_id': self.product.id, 'quantity': 4, 'unit_cost': '2.00'}]})
        self.assertEqual(response.status_code, 201, response.json)
        order = response.json
        response = self.clients['admin'].post(f"/api/purchases/{order['id']}/approve", json={})
        self.assertEqual(response.status_code, 200, response.json)
        return order

    def receive(self, order, quantity):
        response = self.clients['admin'].post(f"/api/purchases/{order['id']}/receptions", json={
            'received_at': datetime.now(inventory_service.COMMERCIAL_ZONE).date().isoformat(),
            'items': [{'line_id': order['items'][0]['id'], 'quantity': quantity}]})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json['receptions'][-1]

    def reconcile(self, action='adjustment'):
        response = self.clients['admin'].post('/api/reconciliation', data={
            'file': (BytesIO(b'product_code;quantity\nTEST-01;14\n'), 'synthetic.csv'),
            'cutoff_at': '2026-09-28T12:00:00-04:00'}, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 201, response.json)
        run = response.json
        url = f"/api/reconciliation/{run['id']}"
        self.assertEqual(self.clients['admin'].post(url + '/confirm', json={}).status_code, 200)
        if action == 'adjustment':  # Only an accredited cut can back a stock adjustment.
            accredit_run(self.app, run['id'])
        payload = {'action': action, 'note': 'Conteo sintético'}
        response = self.clients['admin'].post(url + f"/lines/{run['items'][0]['id']}/resolve", json=payload)
        self.assertEqual(response.status_code, 200, response.json)
        if action == 'adjustment':  # The adjustment is a request that a second administrator approves.
            request = response.json['items'][0]['adjustment_request']
            approved = self.clients['admin2'].post(f"/api/inventory/adjustments/{request['id']}/approve", json={})
            self.assertEqual(approved.status_code, 200, approved.json)
            response = self.clients['admin'].get(url)
        return response.json

    def grant(self, actor, permission):
        role = Role.query.filter_by(slug=self.users[actor].role).one()
        role.permissions.append(Permission.query.filter_by(code=permission).one())
        db.session.commit()
        rbac_service.invalidate_cache()

    def test_sale_reference_and_seller_scope(self):
        sale = self.sale()
        movement_id = sale['items'][0]['stock_movement_id']
        row = self.detail(movement_id)
        self.assertEqual((row['source_type'], row['source_id'], row['display_reference']),
                         ('sale', sale['id'], sale['sale_number']))
        self.assertEqual(row['source_url'], '/sales/' + sale['sale_number'])
        self.assertIsNone(row['creation_origin'])
        self.grant('vendedor', 'inventory:read')
        foreign = self.detail(movement_id, 'vendedor')
        self.assertIsNone(foreign['source_url'])
        self.assertEqual(self.clients['vendedor'].get('/api/sales/' + sale['sale_number']).status_code, 404)
        self.assertEqual(self.clients['vendedor'].get('/sales/' + sale['sale_number']).status_code, 404)
        own = self.sale('vendedor')
        self.assertEqual(self.detail(own['items'][0]['stock_movement_id'], 'vendedor')['source_url'],
                         '/sales/' + own['sale_number'])

    def test_partial_receptions_keep_exact_order_and_receipt(self):
        order = self.purchase()
        first, second = self.receive(order, 1), self.receive(order, 3)
        for receipt in (first, second):
            row = self.detail(receipt['items'][0]['movement_id'])
            self.assertEqual(row['source_type'], 'purchase_reception')
            self.assertEqual(row['source_id'], receipt['id'])
            self.assertEqual(row['display_reference'], f"{order['order_number']} · REC-{receipt['id']:05d}")
            self.assertEqual(row['source_url'], f"/purchases/receptions?detail={receipt['id']}")
            self.assertIsNone(row['creation_origin'])
            detail = self.clients['admin'].get(f"/api/purchases/receptions/{receipt['id']}")
            self.assertEqual(detail.status_code, 200, detail.json)
            self.assertEqual(detail.json['movements'][0]['display_reference'], row['display_reference'])

    def test_reconciliation_with_and_without_adjustment(self):
        run = self.reconcile('acknowledge')
        self.assertIsNone(run['items'][0]['stock_movement_id'])
        self.assertEqual(StockMovement.query.count(), 0)
        run = self.reconcile()
        row = self.detail(run['items'][0]['stock_movement_id'])
        self.assertEqual((row['source_type'], row['source_id'], row['display_reference']),
                         ('reconciliation', run['id'], run['number']))
        self.assertEqual(row['source_url'], f"/reconciliation?detail={run['id']}")
        self.assertIsNone(row['creation_origin'])
        self.assertIsNone(self.detail(row['id'], 'inventario')['source_url'])
        self.assertEqual(self.clients['inventario'].get(f"/api/reconciliation/{run['id']}").status_code, 403)

    def test_manual_reference_external_text_is_not_a_sale(self):
        for external, expected in ((None, None), ('   ', None), ('  VTA-001572  ', 'VTA-001572'), ('x' * 120, 'x' * 120)):
            response = self.manual(external_reference=external)
            self.assertEqual(response.status_code, 201, response.json)
            row = response.json['movement']
            self.assertEqual(row['creation_origin'], 'manual')
            self.assertEqual(row['source_type'], 'manual')
            self.assertEqual(row['source_id'], row['id'])
            self.assertEqual(row['movement_number'], f"MOV-{row['id']:06d}")
            self.assertEqual(row['display_reference'], row['movement_number'])
            self.assertEqual(row['source_url'], f"/inventory?detail={row['id']}")
            self.assertEqual(row['external_reference'], expected)
            self.assertEqual(self.detail(row['id'])['display_reference'], row['display_reference'])
        self.assertEqual(SaleLine.query.count(), 0)

    def test_invalid_external_and_server_fields_do_not_change_stock(self):
        before = self.stock()
        for value in (True, 1, [], {}, 'x' * 121):
            self.assertEqual(self.manual(external_reference=value).status_code, 400)
        for field in SERVER_FIELDS:
            for endpoint, values in (('entry', {'quantity': 1}), ('exit', {'quantity': 1}),
                                     ('adjustment', {'new_stock': 12, 'reason': 'Conteo'})):
                response = self.clients['admin'].post('/api/inventory/' + endpoint,
                    json={'product_id': self.product.id, **values, field: 'spoof'})
                self.assertEqual(response.status_code, 400, (field, response.json))
        for origin in (None, 'initial_stock'):
            with self.assertRaises(ValidationError):
                creation_fields({'external_reference': 'OC-000001'}, origin)
        self.assertEqual(StockMovement.query.count(), 0)
        self.assertEqual(self.stock(), before)

    def test_initial_stock_and_zero(self):
        for quantity in (0, 5):
            response = self.clients['admin'].post('/api/products', json={
                'code': 'INITIAL-' + str(quantity), 'name': 'Producto sintético',
                'category_id': self.product.category_id, 'current_stock': quantity,
                'purchase_price': 2, 'sale_price': 3, 'unit': 'unidad'})
            self.assertEqual(response.status_code, 201, response.json)
            movements = StockMovement.query.filter_by(product_id=response.json['id']).all()
            self.assertEqual(len(movements), int(quantity > 0))
            if movements:
                row = self.detail(movements[0].id)
                self.assertEqual(row['creation_origin'], 'initial_stock')
                self.assertEqual(row['source_type'], 'initial_stock')
                self.assertEqual(row['source_id'], response.json['id'])
                self.assertEqual(row['display_reference'], row['movement_number'])

    def test_origin_document_inputs_reject_authoritative_fields(self):
        customer = Customer(name='Cliente sintético')
        db.session.add(customer)
        db.session.commit()
        sale = {'customer_id': customer.id, 'payment_method': 'cash', 'payment_condition': 'cash',
                'idempotency_key': uuid.uuid4().hex,
                'items': [{'product_id': self.product.id, 'quantity': 1}]}
        product = {'code': 'NO-CREATE', 'name': 'No crear', 'category_id': self.product.category_id}
        for field in SERVER_FIELDS | {'external_reference'}:
            self.assertEqual(self.clients['admin'].post('/api/products', json={**product, field: 'spoof'}).status_code, 400)
            self.assertEqual(self.clients['admin'].post('/api/sales', json={**sale, field: 'spoof'}).status_code, 400)
            nested = {**sale, 'items': [{**sale['items'][0], field: 'spoof'}]}
            self.assertEqual(self.clients['admin'].post('/api/sales', json=nested).status_code, 400)
        self.assertEqual(StockMovement.query.count(), 0)
        self.assertEqual(self.stock(), 10)

    def test_historical_text_never_classifies_and_reads_preserve_rows(self):
        for reason in ('Stock inicial', 'Venta VTA-001572', 'Nota de entrega #NE-000001', 'Cancelación de nota de entrega #NE-000001'):
            db.session.add(StockMovement(product_id=self.product.id, movement_type='entrada', quantity=1,
                                         previous_stock=0, new_stock=1, reason=reason))
        db.session.commit()
        table = StockMovement.__table__
        before = db.session.execute(select(table).order_by(table.c.id)).all()
        with self.app.test_request_context():
            login_user(self.users['admin'])
            for row in inventory_service.query_movements({})['items']:
                self.assertIsNone(row['creation_origin'])
                self.assertIsNone(row['source_type'])
                self.assertIsNone(row['source_url'])
                self.assertEqual(row['source_label'], 'Origen no identificado')
                self.assertEqual(row['display_reference'], row['movement_number'])
        self.assertEqual(before, db.session.execute(select(table).order_by(table.c.id)).all())

    def test_multiple_structural_origins_are_inconsistent(self):
        sale = self.sale()
        movement_id = sale['items'][0]['stock_movement_id']
        run = ReconciliationRun(number='CONC-000042', cutoff_at=datetime.utcnow(), filename='synthetic.csv',
                                sha256='0' * 64, created_by_user_id=self.users['admin'].id, status='confirmed')
        db.session.add(run)
        db.session.flush()
        db.session.add(ReconciliationLine(run_id=run.id, row_number=2, product_code=self.product.code,
            product_id=self.product.id, result='discrepancy', review_status='resolved',
            resolution_action='adjustment', stock_movement_id=movement_id))
        db.session.commit()
        row = self.detail(movement_id)
        self.assertEqual(row['source_type'], 'inconsistent')
        self.assertEqual(row['source_label'], 'Origen inconsistente')
        self.assertEqual(row['display_reference'], row['movement_number'])
        self.assertIsNone(row['source_id'])
        self.assertIsNone(row['source_url'])

    def test_parity_batched_queries_and_read_only(self):
        self.sale()
        order = self.purchase()
        self.receive(order, 1)
        self.reconcile()
        self.manual()
        self.manual(external_reference='OC-000001')
        with self.app.test_request_context():
            login_user(self.users['admin'])
            listed = inventory_service.query_movements({'page': 1, 'per_page': 100})['items']
            references = {row['id']: row['display_reference'] for row in listed}
            counts, statements = [], []
            before = db.session.execute(select(StockMovement.__table__).order_by(StockMovement.id)).all()

            def capture(_connection, _cursor, statement, *_args):
                statements.append(statement.lstrip().split()[0].upper())

            event.listen(db.engine, 'before_cursor_execute', capture)
            try:
                for size in (1, 5):
                    statements.clear()
                    inventory_service.query_movements({'page': 1, 'per_page': size})
                    counts.append(statements.count('SELECT'))
                self.assertEqual(counts[0], counts[1], counts)
                for movement_id, reference in references.items():
                    self.assertEqual(inventory_service.get_movement(movement_id)['display_reference'], reference)
                for row in inventory_service.movement_summary({})['recent']:
                    self.assertEqual(row['display_reference'], references[row['id']])
                for row in inventory_service.query_movements({})['items']:
                    self.assertEqual(row['display_reference'], references[row['id']])
                exported, _, _ = movement_export_service.movement_rows({'sort': 'created_at'})
                expected = [row['display_reference'] for row in sorted(listed, key=lambda row: (row['created_at'], row['id']))]
                self.assertEqual([row['reference'] for row in exported], expected)
                self.assertFalse({'INSERT', 'UPDATE', 'DELETE', 'ALTER', 'CREATE', 'DROP'} & set(statements))
            finally:
                event.remove(db.engine, 'before_cursor_execute', capture)
            self.assertEqual(before, db.session.execute(select(StockMovement.__table__).order_by(StockMovement.id)).all())
            print(f'PASS: list/detail/summary/export parity; SELECT counts 1/5 rows = {counts}; no SQL writes.')
        columns = set(StockMovement.__table__.columns.keys())
        self.assertFalse({'source_type', 'source_id', 'reference_number', 'display_reference', 'movement_number'} & columns)


def serve():
    """Loopback-only synthetic UI fixture; the MySQL-denial guard stays active."""
    from flask import redirect
    from app.services import session_service

    case = TraceabilityTests()
    case.setUp()
    try:
        assert db.engine.url.drivername == 'sqlite' and db.engine.url.database == ':memory:'
        @case.app.get('/_test/session/<actor>')
        def synthetic_session(actor):
            if actor not in case.users:
                return '', 404
            session_service.open_session(case.users[actor])
            return redirect('/inventory')

        case.sale()
        case.receive(case.purchase(), 1)
        case.reconcile()
        case.manual(external_reference='VTA-001572')
        db.session.add(StockMovement(product_id=case.product.id, movement_type='salida', quantity=1,
            previous_stock=10, new_stock=9, reason='Nota de entrega #NE-000001'))
        db.session.commit()
        case.app.run(host='127.0.0.1', port=5046, threaded=False, use_reloader=False, use_debugger=False)
    finally:
        case.doCleanups()


if __name__ == '__main__':
    if '--serve' in sys.argv:
        serve()
    else:
        unittest.main(verbosity=2)
