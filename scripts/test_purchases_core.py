"""Real MariaDB purchase E2E, including simultaneous reception and zero-write reference.

ALL writes assert DATABASE() == ferreteria_conejo_e2e. No SQLite or valuable DB use.
Run: .venv/Scripts/python.exe -B scripts/test_purchases_core.py
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from purchase_e2e_support import make_app, client_for, cleanup, DATABASE
from app.extensions import db
from app.models import Product, StockMovement, Notification, UserSession, User
from app.models.purchase import PurchaseOrder, PurchaseOrderLine, PurchaseReception, PurchaseReceptionLine, Supplier
from app.services import purchase_service, notification_service, settings_service
from app.services.exceptions import ConflictError
from flask_login import login_user


def run():
    app, ids, writes = make_app()
    app.logger.disabled = True
    admin = client_for(app, ids['admin_id'])
    approver = client_for(app, ids['approver_id'])
    reader = client_for(app, ids['reader_id'])
    denied = client_for(app, ids['denied_id'])
    asistente = client_for(app, ids['asistente_id'])
    inventario = client_for(app, ids['inventario_id'])
    checks = []
    api = '/api/purchases'
    today = datetime.now(purchase_service.inventory_service.COMMERCIAL_ZONE).date().isoformat()

    def check(condition, label):
        assert condition, label
        checks.append(label)

    def data(quantity=8, **extras):
        return {'supplier_id': ids['supplier_ids'][0], 'estimated_delivery_date': today,
                'items': [{'product_id': ids['product_ids'][0], 'quantity': quantity, 'unit_cost': '12.25'}], **extras}

    def approve(order):
        response = approver.post(f"{api}/{order['id']}/approve", json={})
        check(response.status_code == 200, 'separate actor approval ' + str(response.json))

    def snapshot():
        with app.app_context():
            return ([db.session.get(Product, pid).current_stock for pid in ids['product_ids']],
                    StockMovement.query.filter(StockMovement.user_id == ids['inventario_id']).count(),
                    PurchaseOrder.query.filter_by(created_by_user_id=ids['asistente_id']).count(),
                    PurchaseReception.query.filter_by(received_by_user_id=ids['inventario_id']).count(),
                    PurchaseOrderLine.query.join(PurchaseOrder).filter(PurchaseOrder.created_by_user_id == ids['asistente_id']).count(),
                    [(line.id, line.quantity_received) for line in PurchaseOrderLine.query.join(PurchaseOrder)
                     .filter(PurchaseOrder.created_by_user_id == ids['asistente_id']).order_by(PurchaseOrderLine.id).all()],
                    PurchaseReceptionLine.query.join(PurchaseReception)
                    .filter(PurchaseReception.received_by_user_id == ids['inventario_id']).count())

    def create(payload=None):
        payload = payload or data()
        quote = asistente.post('/api/supplier-quotes', json={
            'supplier_id': payload['supplier_id'], 'issued_on': today, 'currency': 'VES', 'valid_until': '2099-01-01',
            'items': [{'product_id': item['product_id'], 'quantity': item['quantity'], 'unit_price': item['unit_cost']}
                      for item in payload['items']]})
        check(quote.status_code == 201, 'quote ' + str(quote.json)[:300])
        body = {'supplier_id': payload['supplier_id'], 'supplier_quote_id': quote.json['id'],
                'estimated_delivery_date': payload.get('estimated_delivery_date', today),
                'items': [{'product_id': item['product_id'], 'quantity': item['quantity']} for item in payload['items']]}
        if 'notes' in payload:
            body['notes'] = payload['notes']
        response = asistente.post(api, json=body)
        check(response.status_code == 201, 'create ' + str(response.json)[:400])
        return response.json

    def receive(order, quantities):
        return inventario.post(f"{api}/{order['id']}/receptions", json={'received_at': today,
            'items': [{'line_id': line['id'], 'quantity': quantity} for line, quantity in zip(order['items'], quantities) if quantity]})

    try:
        with app.app_context():
            check(db.session.execute(text('SELECT DATABASE()')).scalar() == DATABASE, 'DATABASE guard verified')
        initial = snapshot()
        order = create(data(items=[{'product_id': ids['product_ids'][0], 'quantity': 8, 'unit_cost': '12.25'},
                                   {'product_id': ids['product_ids'][1], 'quantity': 4, 'unit_cost': '2.10'}]))
        check(order['order_number'] == f"OC-{order['id']:06d}", 'server OC number')
        check(order['total'] == '106.40' and order['subtotal'] == '106.40', 'server Decimal totals')
        check(order['units'] == 12 and order['line_count'] == 2, 'product count means ordered units')
        check(order['received_percent'] == 0 and order['pending_amount'] == '106.40', 'initial derived amounts')
        check(order['creator']['id'] == ids['asistente_id'], 'asistente prepares the order')
        check(admin.post(api, json=data()).status_code == 403, 'admin cannot create an order')
        check(inventario.post(api, json=data()).status_code == 403, 'inventory cannot create an order')
        check(snapshot()[:2] == initial[:2], 'creation no stock or movements')
        zero = create(data(items=[{'product_id': ids['product_ids'][2], 'quantity': 1, 'unit_cost': '0'}]))
        check(zero['total'] == '0.00', 'zero purchase cost allowed')
        for payload in [data(supplier_id=999999999), data(supplier_id=ids['supplier_ids'][3]),
                        data(items=[{'product_id': 999999999, 'quantity': 1, 'unit_cost': '1'}]),
                        data(items=[{'product_id': ids['product_ids'][3], 'quantity': 1, 'unit_cost': '1'}]),
                        data(0), data(-1), data('1.5'), data(True), data(2147483648),
                        data(items=[]), data(items=[data()['items'][0], data()['items'][0]]),
                        data(total='1.00'), data(created_by_user_id=ids['reader_id']), data(order_number='OC-1'),
                        data(status='completed'), data(estimated_delivery_date='2024-99-99')]:
            before = snapshot()
            response = asistente.post(api, json=payload)
            check(response.status_code == 400, 'invalid creation rejected ' + str(payload))
            check(snapshot() == before, 'invalid creation writes nothing')
        for cost in ('-1', 'NaN', 'Infinity', '1.001', '1e10000', None, True, 1.25):
            response = asistente.post('/api/supplier-quotes', json={
                'supplier_id': ids['supplier_ids'][0], 'issued_on': today, 'currency': 'VES', 'valid_until': '2099-01-01',
                'items': [{'product_id': ids['product_ids'][0], 'quantity': 1, 'unit_price': cost}]})
            check(response.status_code == 400, 'invalid money ' + str(cost))
        rollback_quote = asistente.post('/api/supplier-quotes', json={
            'supplier_id': ids['supplier_ids'][0], 'issued_on': today, 'currency': 'VES', 'valid_until': '2099-01-01',
            'items': [{'product_id': ids['product_ids'][0], 'quantity': 1, 'unit_price': '12.25'}]}).json
        before = snapshot()
        with patch.object(db.session, 'commit', side_effect=SQLAlchemyError('synthetic commit failure')), \
                patch.object(notification_service, 'emit') as emit:
            response = asistente.post(api, json={'supplier_id': ids['supplier_ids'][0], 'supplier_quote_id': rollback_quote['id'],
                'items': [{'product_id': ids['product_ids'][0], 'quantity': 1}]})
            check(not emit.called, 'failed creation commit never emits notifications')
        check(response.status_code == 409 and snapshot() == before, 'create atomic rollback')
        check('synthetic' not in str(response.json), 'database exceptions not exposed')

        approve(order)
        check(admin.post(f"{api}/{order['id']}/receptions", json={'received_at': today, 'items': [{'line_id': order['items'][0]['id'], 'quantity': 1}]}).status_code == 403, 'admin cannot receive')
        first = receive(order, [3, 1])
        check(first.status_code == 200, 'one receipt closes the order ' + str(first.json)[:300])
        check(first.json['status'] == 'closed_partial' and first.json['received_percent'] == 33, 'partial close')
        seen = admin.get(f"{api}/{order['id']}").json
        check(seen['pending_amount'] == '67.55' and 'total' not in first.json and 'unit_cost' not in first.json['items'][0], 'inventory receipt hides money; admin keeps the pending amount')
        check(first.json['items'][0]['quantity_not_supplied'] == 5, 'missing quantity stays on the line')
        check(snapshot()[0][:2] == [initial[0][0] + 3, initial[0][1] + 1] and snapshot()[1] == 2, 'receipt increases exact stock with movements')
        check(len(first.json['receptions']) == 1 and len(first.json['receptions'][0]['items']) == 2, 'persisted reception record and lines')
        check(asistente.patch(f"{api}/{order['id']}", json={'notes':'Historial preservado'}).status_code == 409, 'closed order is immutable')
        check(admin.post(f"{api}/{order['id']}/cancel", json={'reason': 'Error posterior'}).status_code == 409, 'received order cannot be voided')
        before = snapshot()
        check(receive(order, [5, 3]).status_code == 409 and snapshot() == before, 'second receipt rejected without writes')
        full = create(data(2))
        approve(full)
        complete = receive(full, [2])
        check(complete.status_code == 200 and complete.json['status'] == 'closed_complete', 'complete receipt closes')
        check(complete.json['received_percent'] == 100 and admin.get(f"{api}/{full['id']}").json['pending_amount'] == '0.00', 'completion totals')
        check(receive(full, [1]).status_code == 409, 'closed complete rejects another receipt')
        check(asistente.patch(f"{api}/{full['id']}", json={'notes':'no'}).status_code == 409, 'completed edit rejected')
        check(admin.post(f"{api}/{full['id']}/cancel", json={'reason': 'Error posterior'}).status_code == 409, 'completed cancellation rejected')

        pending = create()
        other_quote = asistente.post('/api/supplier-quotes', json={
            'supplier_id': ids['supplier_ids'][1], 'issued_on': today, 'currency': 'VES', 'valid_until': '2099-01-01',
            'items': [{'product_id': ids['product_ids'][0], 'quantity': 5, 'unit_price': '12.25'}]}).json
        edited = asistente.patch(f"{api}/{pending['id']}", json={
            'supplier_id': ids['supplier_ids'][1], 'supplier_quote_id': other_quote['id'],
            'items': [{'product_id': ids['product_ids'][0], 'quantity': 5}]})
        check(edited.status_code == 200 and edited.json['total'] == '61.25' and edited.json['supplier']['id'] == ids['supplier_ids'][1], 'pending edit supplier quantities costs')
        duplicate = asistente.post(f"{api}/{order['id']}/duplicate", json={})
        check(duplicate.status_code == 201, 'duplicate completed order')
        check(duplicate.json['order_number'] != order['order_number'] and duplicate.json['received_percent'] == 0 and duplicate.json['status'] == 'pending_approval' and not duplicate.json['receptions'], 'duplicate resets identity receipt state history')
        cancelled = admin.post(f"{api}/{pending['id']}/cancel", json={'reason': 'Error de carga'})
        check(cancelled.status_code == 200 and cancelled.json['status'] == 'cancelled', 'admin voids an unreceived order')
        check(asistente.post(f"{api}/{create()['id']}/cancel", json={'reason': 'No corresponde'}).status_code == 403, 'asistente cannot void')
        check(receive(pending, [1]).status_code == 409, 'cancelled receipt rejected')
        check(asistente.patch(f"{api}/{pending['id']}", json={'notes':'no'}).status_code == 409, 'cancelled edit rejected')
        approve(zero)
        for bad in ({'items':[]}, {'items':[{'line_id':999999, 'quantity':1}]}, {'items':[{'line_id':zero['items'][0]['id'], 'quantity':0}]},
                    {'items':[{'line_id':zero['items'][0]['id'], 'quantity':1}]*2}, {'items':[{'line_id':zero['items'][0]['id'], 'quantity':1}], 'received_at':'2090-01-01'},
                    {'user_id':ids['reader_id'], 'items':[{'line_id':zero['items'][0]['id'], 'quantity':1}]}):
            response = inventario.post(f"{api}/{zero['id']}/receptions", json={'received_at':today, **bad})
            check(response.status_code == 400, 'invalid receipt input rejected')

        rollback = create(data(items=[{'product_id': ids['product_ids'][0], 'quantity': 2, 'unit_cost': '1'}, {'product_id': ids['product_ids'][1], 'quantity':2, 'unit_cost':'1'}]))
        approve(rollback)
        before = snapshot()
        register = purchase_service.inventory_service.register_entry
        calls = []
        def fail_second(data, commit=True):
            calls.append(data)
            if len(calls) == 2:
                raise SQLAlchemyError('synthetic second movement failure')
            check(commit is False, 'InventoryService receives commit=False')
            return register(data, commit=commit)
        with patch.object(purchase_service.inventory_service, 'register_entry', side_effect=fail_second), \
                patch.object(notification_service, 'emit') as emit:
            response = receive(rollback, [1,1])
            check(not emit.called, 'failed reception never emits notifications')
        check(response.status_code == 409 and snapshot() == before, 'receipt rollback includes stock movements history and derived quantities')
        race = create(data(4))
        approve(race)
        racers = [client_for(app, ids['inventario_id']) for _ in range(2)]
        barrier = Barrier(2)
        def race_receive(client):
            barrier.wait(timeout=10)
            return client.post(f"{api}/{race['id']}/receptions", json={'received_at':today, 'items':[{'line_id':race['items'][0]['id'],'quantity':4}]}).status_code
        before = snapshot()
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(race_receive, racers))
        check(sorted(statuses) == [200,409], 'MariaDB simultaneous receive exactly one succeeds')
        check(snapshot()[0][0] == before[0][0] + 4 and snapshot()[1] == before[1] + 1, 'concurrency stock exact, one movement')
        partial_race = create(data(10))
        approve(partial_race)
        barrier = Barrier(2)
        def repeatable_read_race(_):
            with app.test_request_context():
                check(db.session.execute(text('SELECT @@tx_isolation')).scalar() == 'REPEATABLE-READ', 'real default MariaDB isolation')
                login_user(db.session.get(User, ids['inventario_id']))
                # Explicitly establish an old consistent-read snapshot and stale identity map.
                cached = db.session.get(PurchaseOrder, partial_race['id'])
                assert cached.lines[0].quantity_received == 0
                db.session.get(Product, ids['product_ids'][0])
                barrier.wait(timeout=10)
                try:
                    purchase_service.receive_order(partial_race['id'], {'received_at':today,
                        'items':[{'line_id':partial_race['items'][0]['id'],'quantity':6}]})
                    return 200
                except ConflictError:
                    return 409
        before = snapshot()
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(repeatable_read_race, (1,2)))
        check(sorted(statuses) == [200,409], 'REPEATABLE READ partial overreceipt race rejects second six of ten')
        updated = admin.get(f"{api}/{partial_race['id']}").json
        check(updated['received_units'] == 6 and snapshot()[0][0] == before[0][0] + 6 and snapshot()[1] == before[1] + 1,
              'REPEATABLE READ received6 stock+6 exactly one movement')
        check(updated['status'] == 'closed_partial' and len(updated['receptions']) == 1, 'six of ten closes partial with one receipt')
        partial_race = create(data(10))
        approve(partial_race)
        barrier = Barrier(2)
        def halves_race(_):
            with app.test_request_context():
                assert db.session.execute(text('SELECT DATABASE()')).scalar() == DATABASE
                assert db.session.execute(text('SELECT @@tx_isolation')).scalar() == 'REPEATABLE-READ'
                login_user(db.session.get(User, ids['inventario_id']))
                cached = db.session.get(PurchaseOrder, partial_race['id'])
                assert cached.lines[0].quantity_received == 0
                db.session.get(Product, ids['product_ids'][0])
                barrier.wait(timeout=10)
                try:
                    purchase_service.receive_order(partial_race['id'], {'received_at':today,
                        'items':[{'line_id':partial_race['items'][0]['id'],'quantity':5}]})
                    return 200
                except ConflictError:
                    return 409
        before = snapshot()
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(halves_race, (1, 2)))
        updated = admin.get(f"{api}/{partial_race['id']}").json
        check(sorted(statuses) == [200, 409], 'a second reception is rejected even for the remaining half')
        check(updated['received_units'] == 5 and updated['status'] == 'closed_partial', 'first half closes the order')
        check(snapshot()[0][0] == before[0][0] + 5 and snapshot()[1] == before[1] + 1,
              'one reception adds five units and one movement')
        check(len(updated['receptions']) == 1, 'one reception history')
        midnight = create(data(1))
        approve(midnight)
        with app.app_context():
            db.session.get(PurchaseOrder, midnight['id']).ordered_at = datetime.combine(date.fromisoformat(today) + timedelta(days=1), datetime.min.time()) + timedelta(hours=1)
            db.session.commit()
        midnight_detail = admin.get(f"{api}/{midnight['id']}").json
        check(midnight_detail['ordered_date'] == today and midnight_detail['ordered_at'][:10] != today, 'commercial ordered date at UTC midnight boundary')
        check(receive(midnight,[1]).status_code == 200, 'Caracas evening receipt allowed despite next UTC day')

        for route in ('/purchases', api, '/purchases/receptions', '/suppliers', api+'/options'):
            check(reader.get(route).status_code == 200, 'dynamic read role access ' + route)
        check(denied.get(api).status_code == 403 and app.test_client().get(api).status_code == 401, 'RBAC unauthenticated/unauthorized denied')
        for path, method, payload in [(api,'POST',data()), (f"{api}/{zero['id']}",'PATCH',{'notes':'no'}),
                                      (f"{api}/{zero['id']}/receptions",'POST',{}), (f"{api}/{zero['id']}/cancel",'POST',{}),
                                      (f"{api}/{zero['id']}/duplicate",'POST',{}), (api+'/suppliers','POST',{'name':'no'})]:
            check(reader.open(path,method=method,json=payload).status_code == 403, 'mutation permission ' + path)
        check(admin.post(api, json=data(), headers={'Origin':'https://attacker.invalid'}).status_code == 403, 'cross-origin mutation rejected')
        check(admin.post(f"{api}/{zero['id']}/cancel", data={}).status_code == 415, 'form-post mutation rejected')
        check(admin.get(f"{api}/{order['id']}").json['status'] == 'closed_partial', 'eye detail persisted')
        printable = admin.get(f"/purchases/{order['id']}/print")
        check(printable.status_code == 200 and b'ix-sidebar' not in printable.data and order['order_number'].encode() in printable.data, 'shell-less print document')

        formula = asistente.post('/api/suppliers', json={
            'name': '=2+2', 'tax_id': 'CSV-'+ids['run'], 'contact_name': 'Ana', 'phone': '0212',
            'email': 'csv@example.invalid', 'category': 'Ferretería', 'is_active': True})
        check(formula.status_code == 201 and formula.json['approval_status'] == 'pending', 'asistente proposes supplier')
        check(asistente.post(f"/api/suppliers/{formula.json['id']}/approve").status_code == 403, 'asistente cannot approve supplier')
        check(admin.post(api+'/suppliers', json={'name': 'Inline'}).status_code == 403, 'inline supplier stays closed')
        check(admin.post(f"/api/suppliers/{formula.json['id']}/approve", json={}).status_code == 200, 'admin approves supplier')
        ids['supplier_ids'].append(formula.json['id'])
        duplicate_supplier = asistente.post('/api/suppliers', json={
            'name': 'Duplicate', 'tax_id': 'CSV-'+ids['run'], 'contact_name': 'Ana', 'phone': '0212',
            'email': 'csv@example.invalid', 'category': 'Ferretería', 'is_active': True})
        check(duplicate_supplier.status_code == 409, 'supplier unique RIF')
        for i in range(11):
            create(data(1, supplier_id=formula.json['id'], notes='CSV filter batch'))
        selected = admin.get(api,query_string={'supplier_id':formula.json['id'],'per_page':8}).json
        check(selected['pagination']['total'] == 11 and len(selected['items']) == 8, 'real pagination eight')
        page2 = admin.get(api,query_string={'supplier_id':formula.json['id'],'page':2}).json
        check(len(page2['items']) == 3 and page2['pagination']['page'] == 2, 'real second page')
        for size in (20,50):
            check(len(admin.get(api,query_string={'supplier_id':formula.json['id'],'per_page':size}).json['items']) == 11, 'per_page '+str(size))
        for params in ({'per_page':9}, {'page':0}, {'status':'fake'}, {'start':'bad'}, {'start':'2025-01-01','end':'2024-01-01'}):
            check(admin.get(api,query_string=params).status_code == 400, 'filter validation '+str(params))
        for search in (order['order_number'], 'Cemento gris E2E', 'PO-E2E-'+ids['run'], 'E2E Materiales'):
            check(admin.get(api,query_string={'search':search}).json['pagination']['total'] > 0, 'search '+search)
        pending_rows = admin.get(api, query_string={'status': 'pending_approval', 'supplier_id': formula.json['id'], 'per_page': 20}).json
        check(pending_rows['pagination']['total'] == 11 and all(row['status'] == 'pending_approval' for row in pending_rows['items']), 'status filter')
        check(admin.get(api,query_string={'start':'2090-01-01','end':'2090-01-02'}).json['pagination']['total'] == 0, 'date filters real')
        check(admin.get(api,query_string={'start':'','end':''}).json['pagination']['total'] >= 11, 'clear date filters')
        export = admin.get(api+'/export.csv',query_string={'supplier_id':formula.json['id'],'page':2,'per_page':8})
        csv = export.data.decode('utf-8-sig')
        check(export.status_code == 200 and export.data.startswith(b'\xef\xbb\xbf'), 'CSV UTF8 BOM')
        check(len(csv.splitlines()) == 12 and 'Acciones' not in csv, 'CSV all 11 matching rows and no UI columns')
        check("'=2+2" in csv, 'CSV formula-safe supplier')
        check(export.headers['Cache-Control'] == 'no-store' and export.headers['X-Content-Type-Options'] == 'nosniff' and 'attachment' in export.headers['Content-Disposition'], 'CSV hardened headers')
        summary = admin.get(api,query_string={'supplier_id':formula.json['id']}).json
        check(summary['kpis']['count'] == 11 and summary['kpis']['pending'] == 11 and summary['kpis']['total'] == '134.75', 'real KPIs and supplier period totals')
        check(summary['kpis']['top_suppliers'][0]['name'] == '=2+2' and summary['kpis']['top_suppliers'][0]['percent'] == 100, 'real supplier ranking')
        check(summary['kpis']['comparison'] is None, 'honest no previous-period comparison')

        # Make the session old to prove reference does not write last_seen in the global hook.
        with app.app_context():
            UserSession.query.filter_by(user_id=ids['admin_id']).update({'last_seen_at':datetime.utcnow()-timedelta(minutes=5)})
            db.session.commit()
        before_writes = len(writes)
        for path in ('/purchases?ref=1', '/purchases?ref=1&page=2', '/purchases?ref=1&search=Ferrem',
                     api+'?ref=1', api+'/42?ref=1', api+'/options?ref=1', api+'/export.csv?ref=1', '/purchases/42/print?ref=1', '/purchases/receptions?ref=1'):
            check(admin.get(path).status_code == 200, 'reference read ' + path)
        for path in (api, f"{api}/{zero['id']}/duplicate", f"{api}/{zero['id']}/cancel", f"{api}/{zero['id']}/receptions", api+'/suppliers'):
            check(admin.post(path+'?ref=1',json={}).status_code in (403, 409), 'reference mutation guarded '+path)
        check(admin.post(api,json=data(),headers={'Referer':'http://localhost/purchases?ref=1'}).status_code in (403, 409), 'reference stripped query guarded by Referer')
        check(len(writes) == before_writes, 'REFERENCE ZERO SQL WRITES including stale session touch')
        with app.app_context():
            notifications = Notification.query.filter_by(user_id=ids['admin_id'], entity_type='purchase_order').count()
        check(notifications > 0, 'existing notification infrastructure emits real PO events')
        with patch.object(notification_service, 'emit', side_effect=RuntimeError('synthetic notification failure')):
            notified = create()
        check(notified['status'] == 'pending_approval', 'notification failure never reverses successful business result')
        print(f'PASS: {len(checks)} assertions; MariaDB creation/receipt/concurrency/filter/export/action/RBAC/reference checks.')
        print('ORDER_CREATION_STOCK=NO; RECEPTION_INVENTORY_SERVICE=PASS; REFERENCE_WRITES=0; VALUABLE_DB_WRITES=NO')
    finally:
        cleanup(app, ids)
        print('Cleanup: this synthetic run only; E2E database guard enforced.')


if __name__ == '__main__':
    run()
