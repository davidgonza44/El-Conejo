"""Sales report authorization with populated, disposable SQLite fixtures only."""
import csv
import io
import re
import unittest
import zlib
from datetime import datetime
from decimal import Decimal

from approval_test_support import IsolatedCase
from flask_login import login_user
from sqlalchemy import event, select

from app.extensions import db
from app.models import Customer, Product, Role, Sale, SaleLine, StockMovement, User
from app.services import rbac_service, sales_report_service
from test_delivery_note_pdf import extract_pdf_text

TABS = ('summary', 'products', 'customers', 'categories', 'payments')
PARAMS = 'start=2026-06-01&end=2026-10-09'
AMOUNTS = (Decimal('3817.43'), Decimal('997.19'), Decimal('7634.86'), Decimal('4985.95'))


def readable_pdf_text(data):
    """Read actual streams and metadata; compressed random bytes are not text."""
    parts, cursor = [], 0
    for match in re.finditer(rb'\bstream\r?\n', data):
        dictionary = data[:match.start()].rsplit(b'<<', 1)[-1]
        length = re.search(rb'/Length\s+(\d+)\b', dictionary)
        if length is None:
            raise ValueError('PDF stream without a direct length.')
        end = match.end() + int(length.group(1))
        parts.extend((data[cursor:match.start()], zlib.decompress(data[match.end():end])))
        cursor = end
    parts.append(data[cursor:])
    # /ID contiene identificadores binarios del tráiler, no texto del informe.
    # Se conserva el texto de streams y metadatos para detectar importes reales.
    readable = re.sub(rb'/ID\s*\[\s*<[0-9A-Fa-f\s]+>\s*<[0-9A-Fa-f\s]+>\s*\]',
                      b'', b'\n'.join(parts))
    return extract_pdf_text(readable)


class SalesReportSecurity(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.app.config['DEMO_WITHDRAW_CUSTOMER_SALES'] = True
        support = User(name='Soporte sintético', username='reports_support',
                       email='reports-support@example.invalid', role='soporte_ti')
        second = Product(code='PHYSICAL-B', name='Más unidades', category_id=self.product.category_id,
                         current_stock=10, minimum_stock=0, sale_price=AMOUNTS[1], purchase_price=2,
                         sale_price_currency='USD', purchase_price_currency='USD')
        db.session.add_all([support, second])
        db.session.flush()
        self.clients['soporte_ti'] = self.client_for(support)
        self.clients['anonimo'] = self.app.test_client()
        for index, (product, quantity, price) in enumerate(((self.product, 2, AMOUNTS[0]), (second, 5, AMOUNTS[1]))):
            customer = Customer(name=f'Cliente físico {index}', tax_id=f'PHYSICAL-{index}')
            movement = StockMovement(product_id=product.id, movement_type='salida', quantity=quantity,
                                     previous_stock=10, new_stock=10 - quantity, user_id=self.users['vendedor'].id)
            db.session.add_all([customer, movement])
            db.session.flush()
            sale = Sale(customer_id=customer.id, seller_id=self.users['vendedor'].id,
                        sale_number=f'VTA-PHYSICAL-{index}', sold_at=datetime(2026, 9, 20, 16),
                        payment_method='cash' if index else 'transfer', payment_condition='cash',
                        subtotal=price * quantity, total=price * quantity)
            db.session.add(sale)
            db.session.flush()
            db.session.add(SaleLine(sale_id=sale.id, product_id=product.id, stock_movement_id=movement.id,
                                   product_code=product.code, product_name=product.name, quantity=quantity,
                                   unit_price=price, line_subtotal=price * quantity, line_total=price * quantity))
        db.session.commit()
        self.before = self.snapshot()

    def snapshot(self):
        return {model.__tablename__: [tuple(row) for row in db.session.execute(
            select(model.__table__).order_by(model.id))] for model in
            (Product, Sale, SaleLine, StockMovement, Customer, Role)}

    def text(self, response):
        if response.mimetype == 'application/pdf':
            self.assertTrue(response.data.startswith(b'%PDF-'))
            self.assertNotIn(b'/EmbeddedFile', response.data)
            self.assertNotIn(b'/JavaScript', response.data)
            return readable_pdf_text(response.data)
        return response.data.decode('utf-8-sig')

    def assert_physical(self, text):
        self.assertNotIn('$', text)
        for amount in (*AMOUNTS, AMOUNTS[2] + AMOUNTS[3]):
            for value in (f'{amount:.2f}', f'{amount:,.2f}', f'{amount:.2f}'.replace('.', ',')):
                self.assertNotIn(value, text)
        for label in ('Ticket promedio', 'Ingresos por', 'Por ingresos', 'Mayor ticket', 'Participación (%)'):
            self.assertNotIn(label, text)

    def test_all_roles_all_tabs_html_csv_and_real_pdf(self):
        for role in ('admin', 'asistente', 'inventario', 'vendedor', 'soporte_ti', 'anonimo'):
            for tab in TABS:
                for format_name in ('html', 'csv', 'pdf'):
                    path = (f'/reports/sales?tab={tab}&{PARAMS}' if format_name == 'html' else
                            f'/api/reports/sales/export?tab={tab}&format={format_name}&{PARAMS}')
                    with self.subTest(role=role, tab=tab, format=format_name):
                        response = self.clients[role].get(path)
                        expected = (302 if format_name == 'html' else 401) if role == 'anonimo' else (
                            200 if role in ('admin', 'asistente', 'inventario') else 403)
                        self.assertEqual(response.status_code, expected)
                        if expected != 200:
                            continue
                        text = self.text(response)
                        if role == 'inventario':
                            self.assert_physical(text)
                            self.assertIn('Unidades', text)
                            if format_name == 'html':
                                for contract in ('id="rs-config"', 'id="rs-period"', 'data-rs-export="csv"',
                                                 'id="rs-export-pdf"', 'data-rs-per-page'):
                                    self.assertIn(contract, text)
                                for selector in ('data-rs-line=', 'data-rs-bars=', 'data-rs-donut='):
                                    self.assertNotIn(selector, text)
                            if format_name == 'csv':
                                rows = list(csv.DictReader(io.StringIO(text)))
                                self.assertGreater(len(rows), 0)
                        else:
                            self.assertIn('$' if format_name != 'csv' else 'Ingresos' if tab == 'products' else
                                          'Ventas del período' if tab == 'summary' else
                                          'Total' if tab == 'customers' else 'Monto total' if tab == 'payments' else 'Ingresos', text)
        self.assertEqual(self.snapshot(), self.before)

    def test_persisted_revocation_requires_price_permission_even_with_costs(self):
        admin = Role.query.filter_by(slug='admin').one()
        admin.permissions = [permission for permission in admin.permissions if permission.code != 'products:view_price']
        db.session.commit()
        rbac_service.invalidate_cache()
        for tab in TABS:
            for format_name in ('csv', 'pdf'):
                response = self.clients['admin'].get(f'/api/reports/sales/export?tab={tab}&format={format_name}&{PARAMS}')
                self.assertEqual(response.status_code, 200)
                self.assert_physical(self.text(response))

    def test_physical_query_never_selects_or_sorts_by_money(self):
        statements = []

        def observe(_connection, _cursor, statement, _params, _context, _many):
            if statement.lstrip().upper().startswith('SELECT'):
                statements.append(statement.lower())

        with self.app.test_request_context():
            login_user(self.users['inventario'])
            event.listen(db.engine, 'before_cursor_execute', observe)
            try:
                for tab in TABS:
                    data = sales_report_service.report(tab, {'start': '2026-06-01', 'end': '2026-10-09'})
                    self.assertTrue(data['physical'])
                    self.assertGreater(len(data['rows']), 0)
                    self.assertEqual(dict(data['kpis'])['Unidades vendidas'], 7)
                    if tab == 'products':
                        self.assertEqual(data['rows'][0]['code'], 'PHYSICAL-B')
            finally:
                event.remove(db.engine, 'before_cursor_execute', observe)
        for statement in statements:
            for forbidden in ('sales.total', 'sale_lines.line_total', 'unit_price', 'purchase_price', 'sum(sales.total)'):
                self.assertNotIn(forbidden, statement)
        self.assertEqual(self.snapshot(), self.before)

    def test_filters_pagination_empty_and_client_flags_cannot_grant_money(self):
        base = '/api/reports/sales/export?tab=products&format=csv&' + PARAMS
        response = self.clients['inventario'].get(base + '&q=PHYSICAL-B&include_prices=true&role=admin')
        self.assertEqual(response.status_code, 200)
        self.assert_physical(self.text(response))
        rows = list(csv.DictReader(io.StringIO(self.text(response))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['Código'], 'PHYSICAL-B')
        html = self.clients['inventario'].get('/reports/sales?tab=products&' + PARAMS + '&page=999&per_page=20')
        self.assertEqual(html.status_code, 200)
        self.assert_physical(self.text(html))
        for tab in TABS:
            empty = self.clients['inventario'].get(f'/api/reports/sales/export?tab={tab}&format=pdf&start=2026-10-01&end=2026-10-09')
            self.assertEqual(empty.status_code, 200)
            self.assert_physical(self.text(empty))
        invalid = self.clients['inventario'].get(base.replace('tab=products', 'tab=invalid'))
        self.assertEqual(invalid.status_code, 400)


if __name__ == '__main__':
    unittest.main(verbosity=2)
