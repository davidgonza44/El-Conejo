"""Reception contracts; every SQL write is guarded to ferreteria_conejo_e2e."""
from io import BytesIO
from decimal import Decimal
from unittest.mock import patch
from sqlalchemy import text
from openpyxl import load_workbook
from purchase_e2e_support import make_app, client_for, DATABASE
from app.extensions import db
from app.models.purchase import PurchaseReception
from app.services import reception_query_service as queries


def run(*, no_pdf=False):
    app, ids, writes = make_app(seed_orders=True)
    admin = client_for(app, ids['admin_id'])
    reader = client_for(app, ids['reader_id'])
    denied = client_for(app, ids['denied_id'])
    inventario = client_for(app, ids['inventario_id'])
    api = '/api/purchases/receptions'
    checks = []

    def check(condition, label):
        assert condition, label
        checks.append(label)

    def get(params=''):
        response = admin.get(api + params)
        check(response.status_code == 200, 'listing ' + params)
        return response.json

    with app.app_context():
        assert db.session.execute(text('SELECT DATABASE()')).scalar() == DATABASE
    view = get('?search=' + ids['run'])
    check(view['pagination']['total'] == 8, 'actual reception count')
    check(sum(row['status']=='completed' for row in view['items']) == 4, 'completed events')
    check(view['kpis']['count'] >= 8 and view['kpis']['pending'] >= 4, 'real global KPIs')
    with app.app_context():
        check(view['kpis']['count'] == db.session.execute(text('SELECT COUNT(*) FROM purchase_receptions')).scalar(), 'exact total KPI')
        pending = db.session.execute(text("SELECT COUNT(*) FROM purchase_orders p WHERE status = 'approved' AND NOT EXISTS (SELECT 1 FROM purchase_receptions r WHERE r.purchase_order_id = p.id)")).scalar()
        check(view['kpis']['pending'] == pending, 'exact pending work KPI')
        today = queries.datetime.now(queries.COMMERCIAL_ZONE).date()
        amount = db.session.execute(text('SELECT COALESCE(SUM(l.quantity*p.unit_cost),0) FROM purchase_reception_lines l JOIN purchase_order_lines p ON p.id=l.purchase_order_line_id JOIN purchase_receptions r ON r.id=l.reception_id WHERE r.received_at BETWEEN :start AND :end'), {'start':today.replace(day=1),'end':today}).scalar()
        check(Decimal(view['kpis']['amount']) == amount, 'exact commercial month value KPI')
        cumulative, completed = {}, 0
        events = PurchaseReception.query.order_by(PurchaseReception.received_at,PurchaseReception.created_at,PurchaseReception.id).all()
        for event in events:
            totals = cumulative.setdefault(event.purchase_order_id, {})
            for line in event.lines:
                totals[line.purchase_order_line_id] = totals.get(line.purchase_order_line_id, 0) + line.quantity
            completed += all(totals.get(line.id, 0) >= line.quantity_ordered for line in event.order.lines)
        check(view['kpis']['completed'] == completed, 'independent per-line historical completed KPI')
    first = view['items'][0]
    partial = next(row for row in view['items'] if row['status']=='partial')
    order = admin.get('/api/purchases/' + str(partial['order_id'])).json
    response = inventario.post('/api/purchases/' + str(order['id']) + '/receptions', json={
        'received_at': partial['received_at'], 'items':[{'line_id':line['id'], 'quantity':line['quantity_pending']} for line in order['items']]})
    check(response.status_code == 409, 'second receipt rejected')
    check(admin.post('/api/purchases/' + str(order['id']) + '/receptions', json={
        'received_at': partial['received_at'], 'items':[{'line_id':line['id'], 'quantity':1}]}).status_code == 403, 'admin cannot receive')
    check(admin.get(api+'/'+str(partial['id'])).json['status'] == 'partial', 'historical partial remains partial')
    for key, value in [('search',first['number']), ('search',first['order_number']), ('supplier_id',first['supplier']['id']),
        ('search',first['supplier']['name']), ('search', 'PO-E2E-'+ids['run']),
        ('responsible_id',ids['inventario_id']),
        ('status','partial'),('status','completed'), ('start',first['received_at']),('end',first['received_at']),
        ('min_amount',first['amount']),('max_amount',first['amount'])]:
        result = admin.get(api, query_string={key:value})
        check(result.status_code==200 and result.json['pagination']['total']>0, 'filter '+key)
    for sort in queries.SORTS:
        rows = get('?search='+ids['run']+'&per_page=24&sort='+sort)['items']
        key = 'amount' if sort.startswith('amount') else 'id' if sort.startswith('number') else 'received_at'
        values = [Decimal(row[key]) if key=='amount' else row[key] for row in rows]
        check(values == sorted(values, reverse=sort.endswith('desc')), 'sort '+sort)
    view = get('?search='+ids['run']+'&per_page=8')
    check(view['pagination']['total']==8 and len(view['items'])==8, 'pagination count')
    check(get('?search='+ids['run']+'&per_page=8&page=2')['pagination']['page']==1,'page past the end stays on the last page')
    for bad in ('min_amount=NaN','min_amount=10&max_amount=1','start=2026-09-30&end=2026-09-01','sort=evil','per_page=100'):
        check(admin.get(api+'?'+bad).status_code==400,'invalid filter '+bad)
    for fmt in (('csv', 'xlsx') if no_pdf else ('csv', 'xlsx', 'pdf')):
        response = admin.get(api+'/export', query_string={'format':fmt,'search':ids['run'],'per_page':8})
        check(response.status_code==200,'export '+fmt)
        if fmt=='csv':
            check(len(response.data.decode('utf-8-sig').strip().splitlines())==9,'CSV exports all eight matches')
        if fmt=='xlsx':
            check(load_workbook(BytesIO(response.data)).active.max_row==9,'XLSX exports all eight matches')
        if fmt=='pdf':
            check(response.data.startswith(b'%PDF'),'PDF signature')
    export = admin.get(api+'/export', query_string={'format':'csv','search':first['number'], 'include_supplier':'0','include_amount':'0'})
    lines = export.data.decode('utf-8-sig').strip().splitlines()
    check(len(lines)==2 and first['number'] in lines[1], 'export precise filter')
    check('Proveedor' not in lines[0] and 'Monto recibido' not in lines[0], 'export column choices')
    detail = admin.get(api+'/'+str(first['id'])).json
    check(detail['movements'] and detail['items'][0]['movement_id']==detail['movements'][0]['id'],'movement traceability')
    read_paths = [api, '/purchases/receptions', api+'/'+str(first['id']),
                  '/purchases/receptions/'+str(first['id'])+'/print']
    if not no_pdf:
        read_paths.extend([api+'/export?format=pdf', '/purchases/receptions/'+str(first['id'])+'/pdf'])
    for path in read_paths:
        check(reader.get(path).status_code==200,'read permission '+path)
        check(denied.get(path).status_code==403,'denied '+path)
    check(reader.post('/api/purchases/'+str(order['id'])+'/receptions',json={}).status_code==403,'receive permission enforced')
    before = len(writes)
    with patch.object(queries, 'listing', side_effect=AssertionError('Reference queried live receptions')):
        export_format = 'csv' if no_pdf else 'pdf'
        for path in ('/purchases/receptions?ref=1', api+'?ref=1',api+'/options?ref=1',api+'/98?ref=1',api+'/orders/43?ref=1',api+'/export?ref=1&format='+export_format):
            check(admin.get(path).status_code==200,'reference '+path)
    check(admin.post('/api/purchases/'+str(order['id'])+'/receptions?ref=1',json={}).status_code in (403,409),'reference server mutation guard')
    check(len(writes)==before,'REFERENCE WRITES = 0')
    print(f'PASS: {len(checks)} reception assertions; synthetic rows retained only in {DATABASE}; no cleanup SQL.')


if __name__ == '__main__':
    run()
