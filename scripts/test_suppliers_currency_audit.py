"""Supplier currency audit regressions; disposable SQLite, never DEMO/MySQL writes."""
from datetime import datetime, timedelta
from decimal import Decimal
import unittest

from approval_test_support import LinkedSupplierCase
from app.extensions import db
from app.models.purchase import PurchaseOrder
from app.services.inventory_service import COMMERCIAL_ZONE


class SupplierCurrencyAudit(LinkedSupplierCase):
    def order(self, currency, *, legacy=False):
        today = datetime.now(COMMERCIAL_ZONE).date()
        client = self.clients['asistente']
        quoted = client.post('/api/supplier-quotes', json={
            'supplier_id': self.supplier.id, 'currency': currency,
            'issued_on': today.isoformat(),
            'valid_until': (today + timedelta(days=3)).isoformat(),
            'items': [{'product_id': self.product.id, 'quantity': 2, 'unit_price': '8.50'}],
        })
        self.assertEqual(quoted.status_code, 201, quoted.json)
        created = client.post('/api/purchases', json={
            'supplier_id': self.supplier.id, 'supplier_quote_id': quoted.json['id'],
            'items': [{'product_id': self.product.id, 'quantity': 2}],
        })
        self.assertEqual(created.status_code, 201, created.json)
        row = db.session.get(PurchaseOrder, created.json['id'])
        if legacy:
            # Only this disposable fixture is altered to reproduce incomplete history.
            row.supplier_quote_id = None
            db.session.commit()
            db.session.expire_all()
        return row.id

    def listing(self, **params):
        response = self.clients['asistente'].get('/api/suppliers', query_string=params)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def test_usd_ves_grouping_and_symbols_in_both_supplier_surfaces(self):
        self.order('USD')
        self.order('VES')
        expected = [{'currency': 'USD', 'amount': '17.00'},
                    {'currency': 'VES', 'amount': '17.00'}]
        data = self.listing()
        self.assertEqual(data['kpis']['totals_by_currency'], expected)
        self.assertEqual(data['items'][0]['totals_by_currency'], expected)
        html = self.clients['asistente'].get('/suppliers').text
        self.assertEqual(html.count('data-currency="USD">USD · $ 17.00'), 2)
        self.assertEqual(html.count('data-currency="VES">VES · Bs. 17.00'), 2)
        self.assertNotIn('$ 34.00', html)
        self.assertNotIn('Bs. 34.00', html)

    def test_missing_quote_preserves_persisted_ves_in_kpi_row_and_export(self):
        order_id = self.order('VES', legacy=True)
        expected = [{'currency': 'VES', 'amount': '17.00'}]
        data = self.listing()
        self.assertEqual(data['kpis']['totals_by_currency'], expected)
        self.assertEqual(data['items'][0]['totals_by_currency'], expected)
        html = self.clients['asistente'].get('/suppliers').text
        self.assertNotIn('orden histórica sin moneda', html)
        self.assertNotIn('Moneda no registrada · 17.00', html)
        exported = self.clients['asistente'].get('/api/suppliers/export?format=csv').text
        self.assertIn('Órdenes sin moneda', exported)
        self.assertIn('Bs. 17.00', exported)
        self.assertEqual(data['kpis']['unknown_currency_orders'], 0)
        self.assertEqual(data['items'][0]['unknown_currency_orders'], 0)
        detail = self.clients['asistente'].get(f'/api/suppliers/{self.supplier.id}').json
        self.assertEqual(detail['unknown_currency_order_details'], [])
        self.assertNotIn('$ 17.00', html + exported)
        self.assertIn('Bs. 17.00', html + exported)
        db.session.expire_all()
        row = db.session.get(PurchaseOrder, order_id)
        self.assertIsNone(row.supplier_quote_id)
        self.assertEqual(row.currency, 'VES')
        self.assertEqual(row.total, Decimal('17.00'))
        self.assertEqual(self.stock(), 10)

    def test_detached_order_keeps_ves_group_separate_and_comparison_is_unavailable(self):
        self.order('USD')
        self.order('VES')
        self.order('VES', legacy=True)
        data = self.listing()
        expected = [{'currency': 'USD', 'amount': '17.00'},
                    {'currency': 'VES', 'amount': '34.00'}]
        self.assertEqual(data['kpis']['totals_by_currency'], expected)
        self.assertEqual(data['items'][0]['totals_by_currency'], expected)
        self.assertIsNone(data['kpis']['comparison'])
        self.assertEqual(data['kpis']['unknown_currency_orders'], 0)
        self.assertEqual(data['items'][0]['orders'], 3)

    def test_kpi_does_not_expose_a_scalar_total_that_adds_usd_to_ves(self):
        self.order('USD')
        self.order('VES')
        self.assertIsNone(self.listing()['kpis'].get('total'),
                          'A single scalar cannot represent USD + VES.')

    def test_supplier_does_not_expose_a_scalar_total_that_adds_usd_to_ves(self):
        self.order('USD')
        self.order('VES')
        self.assertIsNone(self.listing()['items'][0].get('total'),
                          'A supplier scalar cannot represent USD + VES.')

    def test_amount_filter_without_currency_rejects_mixed_denominations(self):
        self.order('USD')
        self.order('VES')
        response = self.clients['asistente'].get('/api/suppliers?min_total=20')
        self.assertEqual(response.status_code, 400,
                         '17 USD + 17 VES must not satisfy a currency-free minimum of 20.')

    def test_amount_sort_without_currency_rejects_mixed_denominations(self):
        self.order('USD')
        self.order('VES')
        response = self.clients['asistente'].get('/api/suppliers?sort=total_desc')
        self.assertEqual(response.status_code, 400,
                         'Ordering by purchase amount requires a comparable currency.')

    def test_detached_usd_and_ves_orders_keep_their_independent_denominations(self):
        ids = [self.order('USD', legacy=True), self.order('VES', legacy=True)]
        data = self.listing()
        invalid_sum = {'currency': None, 'amount': '34.00'}
        for totals in (data['kpis']['totals_by_currency'], data['items'][0]['totals_by_currency']):
            self.assertNotIn(invalid_sum, totals,
                             'NULL is missing evidence, not a common monetary denomination.')
            self.assertEqual(totals, [{'currency': 'USD', 'amount': '17.00'}, {'currency': 'VES', 'amount': '17.00'}])
        self.assertEqual(data['kpis']['unknown_currency_orders'], 0)
        self.assertEqual(data['items'][0]['unknown_currency_orders'], 0)
        self.assertNotRegex(self.clients['asistente'].get('/suppliers').text,
                            r'<(?:p|small)\b[^>]*class="sp-history-note"')
        detail = self.clients['asistente'].get(f'/api/suppliers/{self.supplier.id}').json
        self.assertEqual(detail['unknown_currency_order_details'], [])
        self.assertEqual({o['id'] for o in detail['recent_orders']}, set(ids))
        self.assertEqual({o['currency'] for o in detail['recent_orders']}, {'USD', 'VES'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
