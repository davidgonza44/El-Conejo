#!/usr/bin/env python3
"""Disposable SQLite export checks. Never loads dotenv or a MySQL database."""
import csv
import gc
import os
import re
import sys
import tempfile
import zlib
from datetime import datetime
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flask import g
from openpyxl import load_workbook
from PIL import Image
from sqlalchemy import event, select
from app.extensions import db
from app.models import Category, Product, StockMovement, User
from app.services import product_export_service as service
from test_products_core import setup_app


def snapshot():
    return {model.__tablename__: [tuple(row) for row in db.session.execute(select(model.__table__)).all()]
            for model in (Product, StockMovement, Category, User)}


def pdf_content(data):
    parts = []
    for stream in re.findall(rb'stream\r?\n(.*?)\r?\nendstream', data, re.S):
        try:
            parts.append(zlib.decompress(stream))
        except zlib.error:
            pass
    return b'\n'.join(parts)


def check_permissions(app, admin, inventory, seller, export):
    assert export(client=app.test_client()).status_code == 401
    with patch('app.utils.auth_decorators.role_has_permission', return_value=False):
        assert export().status_code == 403
    for client in (admin, inventory, seller):
        assert export(client=client).status_code == 200
    for body in ({'format':'exe'}, {'format':'csv','options':{'include_suppliers':True}},
                 {'format':'csv','options':{'include_images':'false'}},
                 {'format':'csv','filters':{'price_min':'NaN'}},
                 {'format':'csv','filters':{'stock_min':1.5}},
                 {'format':'csv','filters':{'updated_from':'bad'}},
                 {'format':'csv','filters':{'sort':'bad'}},
                 {'format':'csv','filters':{'price_min':50,'price_max':1}},
                 {'format':'csv','filters':{'page':2}}):
        assert admin.post('/api/products/export',json=body).status_code == 400, body


def check_csv(admin, export, ids, samples):
    response = export()
    assert response.mimetype == 'text/csv' and 'charset=utf-8' in response.content_type
    assert re.search(r'catalogo_productos_\d{4}-\d{2}-\d{2}_\d{4}\.csv',response.headers['Content-Disposition'])
    assert response.data.startswith(b'\xef\xbb\xbf')
    rows = list(csv.DictReader(StringIO(response.data.decode('utf-8-sig'))))
    assert len(rows)==25 and len(admin.get('/api/products?page=2&per_page=8').json['items'])==8
    names={row['Nombre'] for row in rows}
    assert all("'"+value in names for value in samples[:4])
    assert samples[4] in names
    assert 'Descripción' in rows[0] and 'Precio de compra' in rows[0]
    assert all(row['Precio de compra']=='12.25' for row in rows)
    assert len(list(csv.DictReader(StringIO(export(filters={'category_id':ids['construccion']}).data.decode('utf-8-sig')))))==20
    for filters in ({'search':'EXP-024'}, {'status':'bajo_stock'}, {'stock_min':5,'stock_max':10},
                    {'price_min':25,'price_max':26}, {'updated_from':'2026-09-10','updated_to':'2026-09-10'}, {'sort':'name-desc'}):
        result=list(csv.DictReader(StringIO(export(filters=filters).data.decode('utf-8-sig'))))
        expected=admin.get('/api/products', query_string=filters).json['items']
        assert [r['Código'] for r in result]==[p['code'] for p in expected], filters
    selected=export(options={'include_description':False,'include_stock_prices':False,'include_images':True})
    selected_rows=list(csv.DictReader(StringIO(selected.data.decode('utf-8-sig'))))
    assert list(selected_rows[0])==['Código','Nombre','Categoría','Unidad','Estado','Última actualización','Imagen']
    assert selected_rows[0]['Imagen']=='/media/products/missing.png'


def check_xlsx(export, samples):
    xlsx=export('xlsx',{'include_images':True})
    assert xlsx.status_code==200 and xlsx.mimetype==service.MIMES['xlsx']
    assert '[Content_Types].xml' in ZipFile(BytesIO(xlsx.data)).namelist()
    sheet=load_workbook(BytesIO(xlsx.data))['Productos']
    assert sheet.max_row==26 and sheet.freeze_panes=='A2' and sheet.auto_filter.ref=='A1:N26'
    assert all(cell.font.bold for cell in sheet[1])
    assert all(cell.data_type!='f' for row in sheet for cell in row)
    assert {sheet.cell(r,2).value for r in range(2,27)}.issuperset(samples)
    assert isinstance(sheet['F2'].value,datetime)
    assert sheet['H2'].data_type=='n' and sheet['J2'].value==12.25


def check_pdf(export):
    pdf=export('pdf',{'include_images':True})
    assert pdf.status_code==200 and pdf.mimetype=='application/pdf' and pdf.data.startswith(b'%PDF-')
    content=pdf_content(pdf.data)
    assert b'Cat\xe1logo de productos' in content and b'Descripci\xf3n' in content and b'Precio de' in content
    assert len(re.findall(rb'/Type /Page\b',pdf.data))>1
    assert b'Descripci\xf3n' not in pdf_content(export('pdf',{'include_description':False,'include_stock_prices':False}).data)


def check_images_limits(export, root):
    for fmt in ('csv', 'xlsx', 'pdf'):
        assert export(fmt, filters={'search': 'no-products-match-this'}).status_code == 200
    with patch.object(service,'MAX_PDF_ROWS',10):
        assert export('pdf').status_code == 400
    with patch.object(service,'MAX_EXPORT_ROWS',10):
        assert export().status_code==400
    with patch.object(service,'MAX_TEXT_CHARACTERS',10):
        assert export().status_code==400
    with patch('app.services.product_export_service.thumbnail',return_value=None):
        assert export('pdf',{'include_images':True}).status_code==200
    media=root/'media';(media/'products').mkdir(parents=True)
    Image.new('RGB',(20,30),'red').save(media/'products'/'test.png')
    (media/'products'/'broken.png').write_bytes(b'broken')
    with patch('app.services.upload_service.uploads_root',return_value=str(media)):
        assert service.thumbnail('/media/products/test.png') is not None
        assert service.thumbnail('/media/products/broken.png') is None
        assert service.thumbnail('/media/products/../test.png') is None
        assert service.thumbnail('https://example.invalid/test.png') is None
        data=service.product_values(Product.query.first());data['image_url']='/media/products/test.png'
        columns=service.columns_for(service.OPTIONS|{'include_images':True})
        book=load_workbook(service.xlsx_file([data],columns,datetime.now()))
        assert len(book['Productos']._images)==1
        assert service.pdf_file([data],columns,datetime.now()).getvalue().startswith(b'%PDF-')
    assert service.public_image_url('C:/private/file.png')==''
    assert service.public_image_url('file:///private.png')==''
    assert service.public_image_url('https://example.invalid/image.png')=='https://example.invalid/image.png'


def run():
    with tempfile.TemporaryDirectory(prefix='product-export-') as directory:
        root = Path(directory)
        app, admin, inventory, seller, ids = setup_app(root)
        with app.app_context():
            samples = ['=SUM(1+1)', '+cmd', '@malicious', '-1+2', 'Taladro, "Edición" ñ']
            for i in range(25):
                db.session.add(Product(code=f'EXP-{i:03}', name=samples[i] if i < len(samples) else f'Producto {i:03}',
                    description=('Descripción áéíóú ñ\n' * 100) if i == 0 else None,
                    category_id=ids['construccion'] if i < 20 else ids['electricidad'], unit='Unidad',
                    current_stock=i, minimum_stock=3, purchase_price='12.25', sale_price='25.50',
                    updated_at=datetime(2026, 9, 10, 10, 0), image_url='/media/products/missing.png'))
            db.session.commit()
            before = snapshot()
            writes = []
            def watch(_conn, _cursor, statement, _parameters, _context, _many):
                if statement.lstrip().split()[0].upper() in {'INSERT','UPDATE','DELETE','REPLACE','CREATE','DROP','ALTER'}:
                    writes.append(statement.split()[0])
            event.listen(db.engine, 'before_cursor_execute', watch)
            def export(fmt='csv', options=None, filters=None, client=admin):
                g.pop('_login_user', None)
                return client.post('/api/products/export', json={'format':fmt, 'options':options or {}, 'filters':filters or {}})
            try:
                check_permissions(app, admin, inventory, seller, export)
                check_csv(admin, export, ids, samples)
                check_xlsx(export, samples)
                check_pdf(export)
                check_images_limits(export, root)
                assert snapshot()==before and writes==[], 'Database changed during exports'
            finally:
                event.remove(db.engine,'before_cursor_execute',watch)
                db.session.remove()
                db.engine.dispose()
        print('PASS: permissions, validation, all matching rows, filters, CSV BOM/quoting/formulas, XLSX types/styles/literals/images, multipage PDF, options/limits, no SQL writes; complete table snapshots unchanged.')
        gc.collect()


if __name__=='__main__':
    run()
