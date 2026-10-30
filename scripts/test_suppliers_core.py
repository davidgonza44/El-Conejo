"""Supplier integration against guarded disposable MySQL, never the valuable database."""
from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO
import csv
import json
from unittest.mock import patch

from openpyxl import load_workbook
from sqlalchemy import text
from purchase_e2e_support import make_app, client_for, cleanup, DATABASE
from migrate_suppliers_full import migrate, engine_for
from app.extensions import db
from app.models import Product, StockMovement, Role, Permission
from app.models.purchase import Supplier, PurchaseOrder
from app.services.inventory_service import COMMERCIAL_ZONE, _utc_boundary


def run():
    engine = engine_for(DATABASE)
    assert migrate(engine, DATABASE) == [], 'migration idempotent after first application'
    engine.dispose()
    app, ids, writes = make_app()
    admin, reader, denied = [client_for(app, ids[key]) for key in ('admin_id','reader_id','denied_id')]
    checks = []
    def check(ok, label):
        assert ok, label
        checks.append(label)
    def payload(i=0):
        return dict(name=f'Supplier test {ids["run"]} {i:02d}', tax_id=f'SUP-{ids["run"]}-{i}',
                    contact_name='Contacto de prueba', contact_title='Gerente', phone='0412-5550100',
                    email='supplier@example.invalid', address='Dirección sintética', city='Ciudad prueba',
                    state_province='Estado prueba', category='Clasificación prueba', notes='Prueba E2E',is_active=True)
    def snapshot():
        with app.app_context():
            return ([db.session.get(Product, i).current_stock for i in ids['product_ids']],StockMovement.query.count())
    def get(path):
        response=admin.get(path)
        check(response.status_code==200, 'GET '+path)
        return response.json
    initial = snapshot()

    def check_crud():
        for i in range(10):
            response=admin.post('/api/suppliers',json=payload(i))
            check(response.status_code==201, 'create '+str(i))
            ids['supplier_ids'].append(response.json['id'])
        sid=ids['supplier_ids'][-10]
        root='/api/suppliers/'+str(sid)
        row=get(root)
        check(row['tax_id']==payload()['tax_id'].upper() and row['category']=='Clasificación prueba','canonical fields')
        duplicate=admin.post('/api/suppliers',json=payload())
        check(duplicate.status_code==409,'unique RIF')
        for key in ('name','tax_id','contact_name','phone','email','category'):
            invalid={**payload(99),key:'   '}
            check(admin.post('/api/suppliers',json=invalid).status_code==400,'required '+key)
        for invalid in ({**payload(99),'email':'bad'}, {**payload(99),'is_active':'yes'}, {**payload(99),'notes':'x'*301}, {**payload(99),'total':'10'}):
            check(admin.post('/api/suppliers',json=invalid).status_code==400,'validation')
        edit={**payload(),'name':'  Edited '+ids['run']+'  ','notes':'Updated notes'}
        response=admin.patch(root,json=edit)
        check(response.status_code==200 and response.json['name']==edit['name'].strip(),'edit trim')
        check(response.json['updated_at']>=row['updated_at'],'updated timestamp')
        check(admin.post(root+'/preferred',json={}).status_code==200 and get(root)['is_preferred'],'preferred on')
        check(admin.post(root+'/preferred',json={}).status_code==200 and not get(root)['is_preferred'],'preferred off')
        check(admin.post(root+'/status',json={}).status_code==200 and not get(root)['is_active'],'deactivate')
        check(not any(r['id']==sid for r in get('/api/purchases/options')['suppliers']),'inactive excluded options')
        check(admin.post(root+'/status',json={}).status_code==200 and get(root)['is_active'],'activate')
        check(any(r['id']==sid for r in get('/api/purchases/options')['suppliers']),'canonical purchase options')
        check(snapshot()==initial,'supplier CRUD zero stock effects')
        return sid, root

    def check_orders(sid, root):
        today=datetime.now(COMMERCIAL_ZONE).date()
        po_data={'supplier_id':sid,'items':[{'product_id':ids['product_ids'][0],'quantity':2,'unit_cost':'25.50'}]}
        orders=[]
        for i in range(3):
            response=admin.post('/api/purchases',json=po_data)
            check(response.status_code==201,'purchase integration create')
            orders.append(response.json)
        check(admin.post('/api/purchases/'+str(orders[-1]['id'])+'/cancel',json={}).status_code==200,'cancel purchase')
        with app.app_context():
            assert db.session.execute(text('SELECT DATABASE()')).scalar()==DATABASE
            for i, order in enumerate(orders):
                db.session.get(PurchaseOrder,order['id']).ordered_at=_utc_boundary(today-timedelta(days=2-i))+timedelta(hours=12)
            db.session.commit()
        row=get(root)
        check(row['total']=='102.00' and row['last']==(today-timedelta(days=1)).isoformat(),'real total and last exclude cancelled')
        check(row['orders']==3 and row['pending']==2 and row['completed']==0,'real order statuses')
        check(admin.delete(root,json={}).status_code==409,'safe deletion with orders')
        check(get('/api/purchases/'+str(orders[0]['id']))['supplier']['id']==sid,'purchase FK intact')
        admin.post(root+'/preferred',json={})
        return orders, row

    def check_filters(sid, row):
        prefix='/api/suppliers?category=Clasificaci%C3%B3n+prueba&search='+ids['run']
        data=get(prefix)
        check(data['pagination']['total']==10 and len(data['items'])==8,'real pagination')
        check(len(get(prefix+'&page=2')['items'])==2,'second page')
        for query in ('status=active','category=Clasificaci%C3%B3n+prueba','city=Ciudad+prueba'):
            check(get(prefix+'&'+query)['pagination']['total']==10,'filter '+query)
        check(get(prefix+'&status=inactive')['pagination']['total']==0,'inactive filter')
        for query in ('preferred=yes','min_total=100','max_total=103&min_total=100','last_from='+row['last']+'&last_to='+row['last']):
            result=get(prefix+'&'+query)
            check(result['pagination']['total']==1 and result['items'][0]['id']==sid,'aggregate filter '+query)
        check(get(prefix+'&preferred=no')['pagination']['total']==9,'non preferred filter')
        check(get(prefix+'&city=Other')['pagination']['total']==0,'combined filters')
        for sort in ('name_asc','name_desc','total_asc','total_desc','last_asc','last_desc'):
            result=get(prefix+'&sort='+sort+'&per_page=24')['items']
            if sort.startswith('name'): check([r['name'] for r in result]==sorted([r['name'] for r in result],reverse=sort.endswith('desc')),'sort '+sort)
            elif sort=='total_asc': check(result[-1]['id']==sid,'sort total asc')
            else: check(result[0]['id']==sid,'sort '+sort)
        for query in ('min_total=-1','min_total=10&max_total=1','last_from=bad','last_from=2026-01-02&last_to=2026-01-01','sort=bad','per_page=9'):
            check(admin.get(prefix+'&'+query).status_code==400,'invalid filter '+query)

    def check_exports():
        for fmt in ('csv','xlsx','pdf'):
            response=admin.get('/api/suppliers/export?category=Clasificaci%C3%B3n+prueba&search='+ids['run']+'&page=2&format='+fmt)
            check(response.status_code==200,'export '+fmt)
            if fmt=='csv':
                records=list(csv.reader(response.data.decode('utf-8-sig').splitlines()))
                check(sum(any(ids['run'] in cell for cell in r) for r in records)==10,'CSV all matching rows')
            elif fmt=='xlsx':
                wb=load_workbook(BytesIO(response.data));values=list(wb.active.values)
                check(sum(any(ids['run'] in str(cell) for cell in r) for r in values)==10,'XLSX all matching rows')
            else: check(response.data.startswith(b'%PDF'),'PDF valid')
            response=admin.get('/api/suppliers/export?category=Clasificaci%C3%B3n+prueba&search='+ids['run']+'&preferred=yes&format='+fmt+'&include_contact=0&include_status=0&include_category=0&include_total=0&include_last=0')
            check(response.status_code==200,'filtered optional export '+fmt)
            if fmt=='csv':
                check('supplier@example.invalid' not in response.data.decode('utf-8-sig'),'export options honored')

    def check_rbac(root):
        check(reader.get('/api/suppliers').status_code==200,'reader allowed')
        check(denied.get('/api/suppliers').status_code==403,'read denied')
        for method,path,data in [('post','/api/suppliers',payload()),('patch',root,payload()),('post',root+'/preferred',{}),('post',root+'/status',{}),('delete',root,{})]:
            check(getattr(reader,method)(path,json=data).status_code==403,'write denied '+method+path)
            before=len(writes)
            check(getattr(admin,method)(path+'?ref=1',json=data).status_code==409,'reference denied '+method+path)
            check(getattr(admin,method)(path,json=data,headers={'Referer':'http://localhost/suppliers?ref=1'}).status_code==409,'reference referer denied')
            check(len(writes)==before,'reference mutation zero writes')

    def check_reference():
        before=len(writes)
        first=admin.get('/api/suppliers?ref=1').data
        check(first==admin.get('/api/suppliers?ref=1').data,'deterministic reference')
        for path in ('/suppliers?ref=1','/api/suppliers/1?ref=1','/api/suppliers/export?ref=1&format=csv','/api/suppliers/export?ref=1&format=xlsx','/api/suppliers/export?ref=1&format=pdf'):
            check(admin.get(path).status_code==200,'reference '+path)
        check(len(writes)==before,'reference reads and exports zero writes')

    def check_period(sid, root, orders):
        check(admin.get('/suppliers').status_code==200,'live page')
        check(admin.get('/purchases?new=1&supplier_id='+str(sid)).status_code==200,'preselected purchase page')
        with app.app_context():
            assert db.session.execute(text('SELECT DATABASE()')).scalar()==DATABASE
            for order, stamp in zip(orders, (datetime(2024,5,5,12),datetime(2024,4,5,12),datetime(2024,5,20,12))):
                db.session.get(PurchaseOrder,order['id']).ordered_at=stamp
            role=db.session.get(Role,ids['role_id'])
            role.permissions.append(Permission.query.filter_by(code='purchases:write').one())
            db.session.commit()
        with patch('app.services.supplier_query_service.datetime') as clock:
            clock.now.return_value=datetime(2024,5,20,tzinfo=COMMERCIAL_ZONE)
            indicators=get('/api/suppliers')['kpis']
            check(indicators['total']=='51.00' and indicators['comparison'].startswith('+0.0%'),'commercial monthly real totals and comparable month')
        check(reader.post(root+'/preferred',json={}).status_code==200,'persisted custom role write grant')
        check(admin.post('/api/suppliers',json=payload(99),headers={'Origin':'https://external.invalid'}).status_code==403,'cross origin mutation rejected')

    try:
        with app.app_context():
            check(db.session.execute(text('SELECT DATABASE()')).scalar()==DATABASE,'database guard')
        sid, root = check_crud()
        orders, row = check_orders(sid, root)
        check_filters(sid, row)
        check_exports()
        check_rbac(root)
        check_reference()
        check_period(sid, root, orders)
        delete_id=ids['supplier_ids'][-1]
        check(admin.delete('/api/suppliers/'+str(delete_id),json={}).status_code==200,'safe delete unused supplier')
        check(admin.get('/api/suppliers/'+str(delete_id)).status_code==404,'deleted not found')
        check(snapshot()==initial,'all supplier and order intentions no stock changes')
        print(json.dumps({'passed':len(checks),'checks':checks,'database':DATABASE,'valuable_db_written':False},ensure_ascii=False))
    finally:
        cleanup(app,ids)


if __name__=='__main__':
    run()
