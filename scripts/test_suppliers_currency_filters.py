"""Currency filters/order/export regressions; guarded in-memory SQLite only."""
import csv
from datetime import datetime
from decimal import Decimal
from io import BytesIO, StringIO
import unittest
from unittest.mock import patch

from openpyxl import load_workbook

from approval_test_support import IsolatedCase
from app.extensions import db
from app.models import Product, Role, StockMovement
from app.models.purchase import PurchaseOrder, PurchaseOrderLine, Supplier
from app.models.supplier_quote import SupplierQuote
from app.services import rbac_service
from app.services.inventory_service import COMMERCIAL_ZONE, _utc_boundary


class SupplierCurrencyFilters(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.today = datetime.now(COMMERCIAL_ZONE).date()
        self.suppliers = {'empty': self.supplier}
        for key in ('usd', 'ves', 'both', 'unknown'):
            row = Supplier(name=key, tax_id='TEST-' + key, city='Ciudad sintética', category='Prueba')
            db.session.add(row)
            self.suppliers[key] = row
        db.session.commit()
        self.make_order('usd', 'USD', '20.00')
        self.make_order('ves', 'VES', '1000.00')
        self.make_order('both', 'USD', '10.00')
        self.make_order('both', 'VES', '300.00')
        # Explicit administrative VES fixtures retain their absent quote links.
        self.make_order('unknown', 'VES', '17.00', detached=True)
        self.make_order('unknown', 'VES', '19.00', detached=True)

    def make_order(self, supplier, currency, amount, status='pending_approval', stamp=None, quote=None, detached=False):
        supplier = self.suppliers[supplier]
        if not detached and quote is None:
            quote = SupplierQuote(supplier_id=supplier.id, currency=currency, issued_on=self.today,
                                  valid_until=self.today, created_by_user_id=self.users['asistente'].id)
            db.session.add(quote)
            db.session.flush()
        order = PurchaseOrder(supplier_id=supplier.id, supplier_quote_id=quote.id if quote else None,
                              currency=currency,
                              created_by_user_id=self.users['asistente'].id, subtotal=Decimal(amount),
                              total=Decimal(amount), status=status,
                              ordered_at=stamp or _utc_boundary(self.today))
        db.session.add(order)
        db.session.flush()
        order.order_number = 'TEST-OC-' + str(order.id)
        db.session.commit()
        return order

    def listing(self, **params):
        response = self.clients['asistente'].get('/api/suppliers', query_string=params)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def test_filters_use_selected_currency_and_preserve_nonmonetary_filters(self):
        for currency, minimum, maximum, key in (('USD', '15', '25', 'usd'), ('VES', '900', '1100', 'ves')):
            data = self.listing(currency=currency, min_total=minimum, max_total=maximum,
                                city='Ciudad sintética', status='active', approval_status='approved')
            self.assertEqual(data['pagination']['total'], 1)
            self.assertEqual(data['items'][0]['id'], self.suppliers[key].id)
        self.assertEqual(self.listing(currency='USD', min_total='30')['pagination']['total'], 0)
        self.assertEqual(self.listing(currency='USD', max_total='0')['pagination']['total'], 0)
        self.assertEqual(self.listing(category='Prueba')['pagination']['total'], 4)
        self.assertEqual(self.listing(search='unknown')['items'][0]['orders'], 2)
        self.assertEqual(self.listing(last_from=self.today.isoformat(), last_to=self.today.isoformat())['pagination']['total'], 4)
        # Selecting a denomination alone does not hide the other historical suppliers.
        self.assertEqual(self.listing(currency='VES')['pagination']['total'], 5)

    def test_absent_purchases_always_last_with_stable_id_ties(self):
        for currency, ascending in (('USD', ['both', 'usd']), ('VES', ['unknown', 'both', 'ves'])):
            known_ids = [self.suppliers[key].id for key in ascending]
            missing_ids = sorted(row.id for row in self.suppliers.values() if row.id not in known_ids)
            for sort, expected in (('total_asc', known_ids), ('total_desc', list(reversed(known_ids)))):
                rows = self.listing(currency=currency, sort=sort)['items']
                self.assertEqual([row['id'] for row in rows], expected + missing_ids)
        self.make_order('unknown', 'USD', '0.00')
        first = self.listing(currency='USD', sort='total_asc')['items'][0]
        self.assertEqual(first['id'], self.suppliers['unknown'].id)
        self.assertIn({'currency': 'USD', 'amount': '0.00'}, first['totals_by_currency'])

    def test_currency_validation_covers_page_api_and_export(self):
        client = self.clients['asistente']
        for path in ('/suppliers', '/api/suppliers', '/api/suppliers/export'):
            for params in ({'min_total': '0'}, {'max_total': '50'}, {'sort': 'total_asc'},
                           {'sort': 'total_desc'}, {'currency': 'NULL'}, {'currency': 'EUR'},
                           {'currency': 'USD', 'min_total': '50', 'max_total': '1'}):
                with self.subTest(path=path, params=params):
                    self.assertEqual(client.get(path, query_string=params).status_code, 400)
            self.assertEqual(client.get(path, query_string={'sort': 'name_asc'}).status_code, 200)

    def test_pagination_count_and_export_use_identical_currency_filter(self):
        for index in range(13):
            key = 'tied-' + str(index)
            self.suppliers[key] = Supplier(name='Mismo nombre', tax_id='TEST-TIE-' + str(index))
            db.session.add(self.suppliers[key])
            db.session.commit()
            self.make_order(key, 'USD', '10.00')
        params = dict(currency='USD', min_total='10', max_total='10', sort='total_desc')
        first, second = self.listing(**params), self.listing(**params, page=2)
        ids = [row['id'] for row in first['items'] + second['items']]
        self.assertEqual(first['pagination']['total'], 14)
        self.assertEqual(second['pagination']['total'], 14)
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), 14)
        self.assertEqual(self.listing(**params)['items'], first['items'])
        exported = self.clients['asistente'].get('/api/suppliers/export', query_string={**params, 'page': 2, 'format': 'csv'})
        records = list(csv.DictReader(StringIO(exported.data.decode('utf-8-sig'))))
        self.assertEqual(len(records), 14)
        self.assertEqual({r['RIF'] for r in records}, {r['tax_id'] for r in first['items'] + second['items']})

    def test_statuses_and_shared_quote_do_not_duplicate_totals(self):
        first = PurchaseOrder.query.filter_by(supplier_id=self.suppliers['usd'].id).one()
        extra = Product(code='TEST-SECOND', name='Segundo producto', category_id=self.product.category_id,
                        current_stock=0, minimum_stock=0, purchase_price=1, sale_price=2)
        db.session.add(extra)
        db.session.flush()
        for product in (self.product, extra):
            db.session.add(PurchaseOrderLine(purchase_order_id=first.id, product_id=product.id,
                product_code=product.code, product_name=product.name, quantity_ordered=1,
                unit_cost=10, line_total=10))
        db.session.commit()
        for status in ('pending', 'approved', 'partial', 'completed', 'closed_complete',
                       'closed_partial', 'closed_unavailable', 'closed_price_change', 'cancelled', 'rejected'):
            self.make_order('usd', 'USD', '2.00', status=status, quote=first.supplier_quote)
        data = self.listing(search='usd')['items'][0]
        self.assertEqual(data['totals_by_currency'], [{'currency': 'USD', 'amount': '36.00'}])
        self.assertEqual((data['orders'], data['pending'], data['completed']), (11, 4, 1))
        self.assertEqual(self.listing()['kpis']['totals_by_currency'], [
            {'currency': 'USD', 'amount': '46.00'}, {'currency': 'VES', 'amount': '1336.00'}])
        self.assertEqual(self.stock(), 10)
        self.assertEqual(StockMovement.query.count(), 0)

    def test_22_regularized_orders_keep_prices_and_links_without_unknown_amounts(self):
        for index in range(20):
            self.make_order('unknown', 'VES', str(index + 1), status='cancelled' if index == 0 else 'completed', detached=True)
        before = [(o.id, o.total, o.supplier_quote_id, o.status) for o in PurchaseOrder.query.order_by(PurchaseOrder.id)]
        sid = self.suppliers['unknown'].id
        detail = self.clients['asistente'].get(f'/api/suppliers/{sid}').json
        self.assertEqual(detail['unknown_currency_orders'], 0)
        self.assertEqual(detail['totals_by_currency'], [{'currency': 'VES', 'amount': '245.00'}])
        self.assertEqual(len(detail['recent_orders']), 10)
        self.assertEqual(detail['unknown_currency_order_details'], [])
        self.assertTrue(all(o['currency'] == 'VES' for o in detail['recent_orders']))
        self.assertEqual(self.listing()['kpis']['unknown_currency_orders'], 0)
        for fmt in ('csv', 'xlsx', 'pdf'):
            self.assertEqual(self.clients['asistente'].get('/api/suppliers/export', query_string={'format': fmt}).status_code, 200)
        db.session.expire_all()
        self.assertEqual([(o.id, o.total, o.supplier_quote_id, o.status) for o in PurchaseOrder.query.order_by(PurchaseOrder.id)], before)

    def test_export_columns_are_separate_in_csv_xlsx_and_pdf(self):
        client = self.clients['asistente']
        csv_data = client.get('/api/suppliers/export?format=csv').data.decode('utf-8-sig')
        records = {r['Proveedor']: r for r in csv.DictReader(StringIO(csv_data))}
        self.assertEqual((records['both']['Compras USD'], records['both']['Compras VES']), ('$ 10.00', 'Bs. 300.00'))
        self.assertEqual(records['unknown']['Órdenes sin moneda'], '0')
        self.assertEqual((records['unknown']['Compras USD'], records['unknown']['Compras VES']), ('—', 'Bs. 36.00'))
        workbook = load_workbook(BytesIO(client.get('/api/suppliers/export?format=xlsx').data))
        headers = next(workbook.active.values)
        self.assertTrue({'Compras USD', 'Compras VES', 'Órdenes sin moneda'} <= set(headers))
        with patch('app.services.export_files.pdf_report', return_value=BytesIO(b'%PDF-fixture')) as pdf:
            self.assertEqual(client.get('/api/suppliers/export?format=pdf').status_code, 200)
            self.assertTrue({'total_usd', 'total_ves', 'unknown_currency_orders'} <= {c[0] for c in pdf.call_args.args[3]})
        self.assertNotIn('Compras USD', client.get('/api/suppliers/export?include_total=0').text)
        html = client.get('/suppliers?currency=VES&min_total=1&sort=total_desc').text
        self.assertIn('name="currency" value="VES"', html)
        self.assertIn('USD ($)', html)
        self.assertIn('VES (Bs.)', html)

    def test_comparison_and_cost_privacy_preserve_existing_contracts(self):
        self.make_order('usd', 'USD', '20', stamp=datetime(2024, 4, 5, 12))
        current = self.make_order('usd', 'USD', '30', stamp=datetime(2024, 5, 5, 12))
        with patch('app.services.supplier_query_service.datetime') as clock:
            clock.now.return_value = datetime(2024, 5, 20, tzinfo=COMMERCIAL_ZONE)
            self.assertEqual(self.listing()['kpis']['comparison'], '+50.0% vs. mes anterior comparable')
            self.make_order('usd', 'VES', '99', stamp=datetime(2024, 5, 5, 12), detached=True)
            self.assertIsNone(self.listing()['kpis']['comparison'])
        role = Role.query.filter_by(slug='asistente').one()
        role.permissions = [p for p in role.permissions if p.code != 'products:view_cost']
        db.session.commit()
        rbac_service.invalidate_cache()
        listing = self.listing()
        self.assertEqual(listing['kpis']['totals_by_currency'], [])
        self.assertTrue(all('totals_by_currency' not in row and 'total' not in row for row in listing['items']))
        detail = self.clients['asistente'].get(f'/api/suppliers/{self.suppliers["usd"].id}').json
        self.assertTrue(all('total' not in o for o in detail['recent_orders'] + detail['unknown_currency_order_details']))
        self.assertEqual(self.clients['asistente'].get('/api/suppliers?currency=USD&sort=total_desc').status_code, 403)
        for path in ('/suppliers', '/api/suppliers', '/api/suppliers/export', f'/api/suppliers/{self.supplier.id}'):
            self.assertEqual(self.clients['inventario'].get(path).status_code, 403)
        inventory = self.clients['inventario'].get(f'/api/purchases/{current.id}').json
        self.assertNotIn('total', inventory)


if __name__ == '__main__':
    unittest.main(verbosity=2)
