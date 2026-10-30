#!/usr/bin/env python3
"""Movement + category export checks against the disposable ferreteria_conejo_e2e database ONLY.

Seeds a synthetic run (users, category, products, movements), exercises the export
endpoints with SQL-write monitoring, then removes its own synthetic rows.
Never loads dotenv or touches the operational database.
Run: python -B scripts/test_export_e2e.py
"""
import csv
import os
import re
import secrets
import sys
import zlib
from datetime import datetime
from decimal import Decimal
from io import BytesIO, StringIO
from pathlib import Path

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from flask import g
from flask_login import login_user
from openpyxl import load_workbook
from sqlalchemy import bindparam, event, inspect, text

from app import create_app
from app.extensions import db
from app.models import Category, Product, StockMovement, User
from app.services import inventory_service, product_service
from serve_inventory_e2e import E2EConfig

DATABASE = 'ferreteria_conejo_e2e'
PASSWORD = 'DisposableExportOnly123!'
WRITES = {'INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'CREATE', 'DROP', 'ALTER'}


def pdf_text(data):
    parts = []
    for stream in re.findall(rb'stream\r?\n(.*?)\r?\nendstream', data, re.S):
        try:
            parts.append(zlib.decompress(stream))
        except zlib.error:
            pass
    return b'\n'.join(parts)


def rows_of(response):
    assert response.status_code == 200, response.data[:200]
    assert response.data.startswith(b'\xef\xbb\xbf')
    return list(csv.DictReader(StringIO(response.data.decode('utf-8-sig'))))


def seed(app, run):
    with app.app_context():
        users = {}
        for role in ('admin', 'inventario', 'vendedor'):
            user = User(name=f'Export {role} {run}', username=f'exp_{role}_{run}', email=f'exp_{role}_{run}@example.invalid', role=role)
            user.set_password(PASSWORD)
            users[role] = user
        active = Category(name=f'Export Activa {run}', description=f'desc {run} activa', color='green')
        inactive = Category(name=f'Export Inactiva {run}', description=f'desc {run} inactiva', is_active=False)
        empty = Category(name=f'Export Vacía {run}', description=f'desc {run} vacía')
        db.session.add_all([*users.values(), active, inactive, empty])
        db.session.commit()
        with app.test_request_context():
            login_user(users['admin'])
            products = [product_service.create_product({'code': f'EXP-{run}-{i}', 'name': f'Producto exportable {run} {i}',
                                                        'category_id': active.id, 'current_stock': 50, 'purchase_price': '10.50'})
                        for i in range(3)]
            for i in range(10):
                inventory_service.register_exit({'product_id': products[i % 3].id, 'quantity': 1 + i % 3, 'reason': f'salida {run} {i}'})
            login_user(users['inventario'])
            for i in range(6):
                inventory_service.register_entry({'product_id': products[i % 3].id, 'quantity': 4, 'reason': f'entrada {run} {i}'})
            inventory_service.register_adjustment({'product_id': products[0].id, 'new_stock': 40, 'reason': f'ajuste {run}'})
        return {'users': {role: user.id for role, user in users.items()}, 'categories': [active.id, inactive.id, empty.id],
                'products': [p.id for p in products], 'active_id': active.id}


def cleanup(app, ids):
    with app.app_context():
        StockMovement.query.filter(StockMovement.product_id.in_(ids['products'])).delete(synchronize_session=False)
        if inspect(db.engine).has_table('product_state_histories'):
            db.session.execute(text('DELETE FROM product_state_histories WHERE product_id IN :ids').bindparams(
                bindparam('ids', expanding=True)), {'ids': ids['products']})
        Product.query.filter(Product.id.in_(ids['products'])).delete(synchronize_session=False)
        Category.query.filter(Category.id.in_(ids['categories'])).delete(synchronize_session=False)
        User.query.filter(User.id.in_(list(ids['users'].values()))).delete(synchronize_session=False)
        db.session.commit()


def login(app, run, role):
    client = app.test_client()
    assert client.post('/api/auth/login', json={'identifier': f'exp_{role}_{run}', 'password': PASSWORD}).status_code == 200
    return client


def check_movements(app, run, clients, ids):
    admin = clients['admin']
    export = lambda client=admin, **params: (g.pop('_login_user', None), client.get('/api/inventory/movements/export', query_string=params))[1]
    total = admin.get('/api/inventory/movements', query_string={'search': run, 'page': 1, 'per_page': 8}).json
    assert total['total'] == 20 and total['count'] == 8, f'fixture must span several pages: {total}'

    xlsx = export(format='xlsx', search=run)
    assert xlsx.status_code == 200 and xlsx.mimetype.endswith('spreadsheetml.sheet')
    assert re.search(r'movimientos_inventario_\d{4}-\d{2}-\d{2}\.xlsx', xlsx.headers['Content-Disposition'])
    sheet = load_workbook(BytesIO(xlsx.data))['Movimientos']
    header = [cell.value for cell in sheet[1]]
    assert header == ['Fecha', 'Tipo', 'Referencia', 'Producto', 'Código', 'Cantidad', 'Stock anterior', 'Stock actual', 'Responsable', 'Motivo']
    assert 'Sucursal' not in header and 'Estado' not in header
    assert sheet.max_row == 21 and all(cell.font.bold for cell in sheet[1])
    assert isinstance(sheet['A2'].value, datetime)
    assert all(sheet.cell(r, c).data_type == 'n' for r in range(2, 22) for c in (6, 7, 8))
    assert all(re.fullmatch(r'MOV-\d{6}', sheet.cell(r, 3).value) for r in range(2, 22))
    first = sheet['A2'].value
    assert sheet['B2'].value == 'Ajuste' and sheet['F2'].value == 40 - sheet['G2'].value and sheet['I2'].value == f'Export inventario {run}'
    assert sheet['D2'].value.startswith('Producto exportable') and sheet['E2'].value == f'EXP-{run}-0'

    rows = rows_of(export(format='csv', search=run))
    assert len(rows) == 20 and list(rows[0]) == header
    assert re.search(r'movimientos_inventario_\d{4}-\d{2}-\d{2}\.csv', export(format='csv', search=run).headers['Content-Disposition'])
    assert {row['Tipo'] for row in rows} == {'Entrada', 'Salida', 'Ajuste'}
    assert all(row['Cantidad'].startswith('-') for row in rows if row['Tipo'] == 'Salida')
    assert {row['Tipo'] for row in rows_of(export(format='csv', search=run, movement_type='salida'))} == {'Salida'}
    assert len(rows_of(export(format='csv', search=run, movement_type='salida'))) == 10
    by_user = rows_of(export(format='csv', search=run, user_id=ids['users']['inventario']))
    assert len(by_user) == 7 and {row['Responsable'] for row in by_user} == {f'Export inventario {run}'}
    today = first.date().isoformat()
    assert len(rows_of(export(format='csv', search=run, date_from=today, date_to=today))) == 20
    assert rows_of(export(format='csv', search=run, date_to='2000-01-01')) == []
    ordered = [abs(int(row['Cantidad'])) for row in rows_of(export(format='csv', search=run, sort='quantity', movement_type='salida'))]
    assert ordered == sorted(ordered) and ordered[0] == 1 and ordered[-1] == 3

    pdf = export(format='pdf', search=run)
    assert pdf.status_code == 200 and pdf.mimetype == 'application/pdf' and pdf.data.startswith(b'%PDF-') and len(pdf.data) > 1500
    content = pdf_text(pdf.data)
    assert b'Movimientos de Inventario' in content and 'Ferretería y Construcciones El Conejo C.A.'.encode('cp1252') in content and b'Tipo: Salida' in pdf_text(export(format='pdf', search=run, movement_type='salida').data)

    for params in ({'format': 'exe'}, {}, {'format': 'csv', 'branch': '1'}, {'format': 'csv', 'movement_type': 'transferencia'},
                   {'format': 'csv', 'sort': 'id;drop'}, {'format': 'csv', 'date_from': 'ayer'}, {'format': 'csv', 'user_id': '0'}):
        assert export(**params).status_code == 400, params
    assert export(client=app.test_client(), format='csv').status_code == 401
    assert export(client=clients['vendedor'], format='csv').status_code == 403
    assert export(client=clients['inventario'], format='csv', search=run).status_code == 200

    fresh = lambda client, **params: (g.pop('_login_user', None), client.get('/inventory/print', query_string=params))[1]
    page = fresh(admin, search=run, movement_type='salida')
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and 'Movimientos de Inventario' in html and 'ix-sidebar' not in html
    assert html.count('<tr>') == 11 and 'Tipo: Salida' in html and 'window.print()' in html
    assert fresh(clients['vendedor']).status_code == 302
    assert fresh(app.test_client()).status_code == 302
    assert fresh(admin, branch='1').status_code == 400
    print('PASS movements: xlsx/csv/pdf, all 20 rows across pages, filters (type/user/date/sort), no fabricated columns, 400s, permissions, print page')


def check_categories(app, run, clients, ids):
    admin = clients['admin']
    export = lambda client=admin, **params: (g.pop('_login_user', None), client.get('/api/categories/export', query_string=params))[1]
    assert len(admin.get('/api/categories', query_string={'search': run, 'page': 1, 'per_page': 2}).json['items']) == 2

    xlsx = export(format='xlsx', search=run, sort='value-desc')
    assert xlsx.status_code == 200 and re.search(r'categorias_\d{4}-\d{2}-\d{2}\.xlsx', xlsx.headers['Content-Disposition'])
    sheet = load_workbook(BytesIO(xlsx.data))['Categorías']
    assert [c.value for c in sheet[1]] == ['Categoría', 'Descripción', 'Estado', 'Productos', 'Valor en inventario']
    assert sheet.max_row == 4 and sheet['A2'].value == f'Export Activa {run}'
    assert sheet['D2'].data_type == 'n' and sheet['D2'].value == 3
    assert sheet['E2'].data_type == 'n'
    expected_value = admin.get(f"/api/categories/{ids['active_id']}").json['inventory_value']
    assert float(sheet['E2'].value) == expected_value and expected_value > 0
    assert sheet['E2'].number_format == '0.00'

    rows = rows_of(export(format='csv', search=run))
    assert len(rows) == 3 and [r['Categoría'] for r in rows] == sorted(r['Categoría'] for r in rows)
    assert {r['Estado'] for r in rows} == {'Activa', 'Inactiva'}
    inactive = rows_of(export(format='csv', search=run, status='inactive'))
    assert [r['Categoría'] for r in inactive] == [f'Export Inactiva {run}'] and inactive[0]['Estado'] == 'Inactiva'
    assert len(rows_of(export(format='csv', search=run, status='active'))) == 2
    assert rows_of(export(format='csv', search=run, sort='value-desc'))[0]['Categoría'] == f'Export Activa {run}'
    only_name = rows_of(export(format='csv', search=run, include_description='0', include_status='0', include_products='0', include_value='0'))
    assert list(only_name[0]) == ['Categoría'] and len(only_name) == 3
    partial = rows_of(export(format='csv', search=run, include_description='0', include_status='1', include_products='1', include_value='0'))
    assert list(partial[0]) == ['Categoría', 'Estado', 'Productos']
    assert next(r for r in partial if r['Categoría'] == f'Export Vacía {run}')['Productos'] == '0'
    assert rows[0]['Descripción'] == f'desc {run} activa'

    pdf = export(format='pdf', search=run, include_description='0')
    assert pdf.status_code == 200 and pdf.mimetype == 'application/pdf' and pdf.data.startswith(b'%PDF-') and len(pdf.data) > 1000
    content = pdf_text(pdf.data)
    assert b'Listado de categor\xedas' in content and b'Campos: Categor\xeda, Estado, Productos' in content

    for params in ({'format': 'doc'}, {}, {'format': 'csv', 'include_status': 'maybe'}, {'format': 'csv', 'status': 'raro'}, {'format': 'csv', 'sort': 'id;drop'}):
        assert export(**params).status_code == 400, params
    assert export(client=app.test_client(), format='csv').status_code == 401
    assert export(client=clients['vendedor'], format='csv', search=run).status_code == 200
    print('PASS categories: xlsx/csv/pdf, filters (search/status/sort), all rows, options on/off, inactive label, real aggregates, 400s, permissions')


def main():
    app = create_app(E2EConfig)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, DEBUG=False)
    with app.app_context():
        assert db.engine.url.database == DATABASE and db.session.execute(text('SELECT DATABASE()')).scalar() == DATABASE
    run = secrets.token_hex(3)
    ids = seed(app, run)
    writes = []

    def watch(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().split()[0].upper() in WRITES:
            writes.append(statement.split()[0])
    try:
        clients = {role: login(app, run, role) for role in ('admin', 'inventario', 'vendedor')}
        with app.app_context():
            before = [tuple(r) for r in db.session.execute(text('SELECT id, current_stock FROM products ORDER BY id')).all()]
            before_mov = db.session.execute(text('SELECT COUNT(*), MAX(id) FROM stock_movements')).one()
            event.listen(db.engine, 'before_cursor_execute', watch)
            try:
                check_movements(app, run, clients, ids)
                check_categories(app, run, clients, ids)
            finally:
                event.remove(db.engine, 'before_cursor_execute', watch)
            db.session.remove()
            after = [tuple(r) for r in db.session.execute(text('SELECT id, current_stock FROM products ORDER BY id')).all()]
            after_mov = db.session.execute(text('SELECT COUNT(*), MAX(id) FROM stock_movements')).one()
        assert writes == [] and before == after and tuple(before_mov) == tuple(after_mov), f'writes during export: {writes}'
        print('PASS: zero SQL writes during exports; stock and movement tables unchanged')
    finally:
        cleanup(app, ids)


if __name__ == '__main__':
    main()
