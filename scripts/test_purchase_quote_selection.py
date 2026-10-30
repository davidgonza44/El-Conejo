"""Quote-dependent purchase options and save validation; isolated SQLite only."""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from approval_test_support import LinkedSupplierCase as IsolatedCase
from app.extensions import db
from app.models import Product
from app.models.purchase import Supplier, PurchaseOrder
from app.models.supplier_quote import SupplierQuote


class PurchaseQuoteTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.today = datetime.now(ZoneInfo('America/Caracas')).date()
        self.client = self.clients['asistente']
        self.other = Supplier(name='Otro proveedor sintético', tax_id='TEST-OTHER')
        self.extra = Product(code='TEST-02', name='Otro producto', category_id=self.product.category_id,
                             current_stock=0, minimum_stock=0, purchase_price=1, sale_price=2, is_active=True)
        db.session.add_all([self.other, self.extra])
        db.session.commit()
        self.associate(self.product, self.other)
        self.associate(self.extra, self.supplier)

    def quote(self, supplier=None, product=None, price='17.25', **extra):
        result = self.client.post('/api/supplier-quotes', json={
            'supplier_id': (supplier or self.supplier).id, 'issued_on': self.today.isoformat(),
            'valid_until': (self.today + timedelta(days=30)).isoformat(), 'currency': 'VES',
            'items': [{'product_id': (product or self.product).id, 'quantity': 2, 'unit_price': price}], **extra})
        self.assertEqual(result.status_code, 201, result.get_json())
        return result.get_json()

    def options(self, supplier=None, quote=None):
        query = {}
        if supplier:
            query['supplier_id'] = supplier.id
        if quote:
            query['supplier_quote_id'] = quote['id']
        return self.client.get('/api/purchases/options', query_string=query)

    def payload(self, quote, supplier=None, product=None):
        return {'supplier_id': (supplier or self.supplier).id, 'supplier_quote_id': quote['id'],
                'items': [{'product_id': (product or self.product).id, 'quantity': 2, 'unit_cost': '0.01'}]}

    def test_empty_and_supplier_scoped_options(self):
        self.assertEqual(self.options().get_json()['quotes'], [])
        self.assertEqual(self.options(self.other).get_json()['quotes'], [])
        first = self.quote()
        second = self.quote(self.other)
        self.assertEqual([q['id'] for q in self.options(self.supplier).get_json()['quotes']], [first['id']])
        self.assertEqual([q['id'] for q in self.options(self.other).get_json()['quotes']], [second['id']])

    def test_quote_products_prices_and_foreign_supplier(self):
        first = self.quote()
        second = self.quote(product=self.extra, price='4.50')
        for quote, product, price in [(first, self.product, '17.25'), (second, self.extra, '4.50')]:
            response = self.options(self.supplier, quote)
            self.assertEqual(response.status_code, 200)
            self.assertEqual([(p['id'], p['unit_cost']) for p in response.get_json()['products']], [(product.id, price)])
        self.assertEqual(self.options(self.other, first).status_code, 400)
        self.assertEqual(self.client.post('/api/purchases', json=self.payload(first, self.other)).status_code, 400)
        self.assertEqual(self.client.post('/api/purchases', json=self.payload(first, product=self.extra)).status_code, 400)
        result = self.client.post('/api/purchases', json=self.payload(first))
        self.assertEqual(result.status_code, 201, result.get_json())
        self.assertEqual(result.get_json()['items'][0]['unit_cost'], '17.25')

    def test_invalidated_after_selection_rejected_on_create_and_edit(self):
        for status in ('vencida', 'reemplazada', 'cancelada', 'expired_date'):
            with self.subTest(status=status):
                quote = self.quote()
                created = self.client.post('/api/purchases', json=self.payload(quote))
                self.assertEqual(created.status_code, 201)
                row = db.session.get(SupplierQuote, quote['id'])
                if status == 'expired_date':
                    row.valid_until = self.today - timedelta(days=1)
                else:
                    row.status = status
                db.session.commit()
                count = PurchaseOrder.query.count()
                self.assertNotIn(quote['id'], [q['id'] for q in self.options(self.supplier).get_json()['quotes']])
                self.assertEqual(self.options(self.supplier, quote).status_code, 400)
                self.assertEqual(self.client.post('/api/purchases', json=self.payload(quote)).status_code, 400)
                self.assertEqual(self.client.patch('/api/purchases/' + str(created.get_json()['id']), json=self.payload(quote)).status_code, 400)
                self.assertEqual(self.client.patch('/api/purchases/' + str(created.get_json()['id']), json={'notes': 'Cambio de notas'}).status_code, 400)
                self.assertEqual(PurchaseOrder.query.count(), count)

    def test_valid_through_today_and_replacement(self):
        quote = self.quote(valid_until=self.today.isoformat())
        self.assertEqual(self.options(self.supplier, quote).status_code, 200)
        replacement = self.quote(replaces_quote_id=quote['id'])
        self.assertEqual([q['id'] for q in self.options(self.supplier).get_json()['quotes']], [replacement['id']])
        self.assertEqual(self.client.post('/api/purchases', json=self.payload(quote)).status_code, 400)


if __name__ == '__main__':
    unittest.main(verbosity=2)
