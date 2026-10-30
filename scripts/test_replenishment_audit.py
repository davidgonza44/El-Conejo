"""Adversarial SR regression suite. In-memory SQLite; MySQL/delivery forbidden."""
from datetime import datetime, timedelta

from approval_test_support import LinkedSupplierCase
from app.extensions import db
from app.models.purchase import Supplier, PurchaseOrder, PurchaseReception
from app.models.replenishment_request import ReplenishmentRequest
from app.models.supplier_quote import SupplierQuote
from app.services.inventory_service import COMMERCIAL_ZONE


class RequestAudit(LinkedSupplierCase):
    def test_client_money_and_huge_integer_rejected(self):
        for field in ('unit_cost', 'cost', 'purchase_price', 'sale_price', 'subtotal', 'total'):
            response = self.clients['inventario'].post('/api/replenishment-requests', json={
                'items': [{'product_id': self.product.id, 'quantity': 2, field: '1.00'}]})
            self.assertEqual(response.status_code, 400)
        response = self.clients['inventario'].post('/api/replenishment-requests', json={
            'items': [{'product_id': self.product.id, 'quantity': '9' * 5000}]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ReplenishmentRequest.query.count(), 0)

    def sr(self):
        response = self.clients['inventario'].post('/api/replenishment-requests', json={
            'items': [{'product_id': self.product.id, 'quantity': 2}]})
        self.assertEqual(response.status_code, 201)
        return response.json['id']

    def start(self, key):
        return self.clients['asistente'].post(f'/api/replenishment-requests/{key}/start', json={})

    def quote(self, key, **changes):
        today = datetime.now(COMMERCIAL_ZONE).date()
        data = {'supplier_id': self.supplier.id, 'currency': 'VES', 'issued_on': today.isoformat(),
                'valid_until': (today + timedelta(days=2)).isoformat(),
                'items': [{'product_id': self.product.id, 'unit_price': '2.00'}]}
        return self.clients['asistente'].post(f'/api/replenishment-requests/{key}/quotes',
                                             json={**data, **changes})

    def prepare(self, key, quote):
        return self.clients['asistente'].post(f'/api/replenishment-requests/{key}/prepare',
                                             json={'supplier_quote_id': quote})

    def test_start_trace_and_duplicate(self):
        key = self.sr()
        row = self.start(key).json
        self.assertIsNotNone(row['processed_by'])
        self.assertEqual(row['processed_by']['id'], self.users['asistente'].id)
        self.assertIsNotNone(row['processed_at'])
        self.assertEqual(self.start(key).status_code, 409)
        db.session.expire_all()
        self.assertIsNotNone(db.session.get(ReplenishmentRequest, key).processed_at)

    def test_zero_quote_rejected(self):
        key = self.sr()
        self.start(key)
        self.assertEqual(self.quote(key, items=[{'product_id': self.product.id, 'unit_price': '0'}]).status_code, 400)

    def test_nonstring_currency_controlled(self):
        key = self.sr()
        self.start(key)
        self.assertEqual(self.quote(key, currency=123).status_code, 400)

    def test_unlinked_legacy_quote_not_offered_or_converted(self):
        key = self.sr()
        self.start(key)
        quote = self.quote(key).json['id']
        other = Supplier(name='Unlinked', tax_id='UNLINKED', is_active=True, approval_status='approved')
        db.session.add(other)
        db.session.flush()
        db.session.get(SupplierQuote, quote).supplier_id = other.id
        db.session.commit()
        html = self.clients['asistente'].get(f'/purchases/requests/{key}').text
        self.assertNotIn(f'<option value="{quote}">CP-', html)
        self.assertEqual(self.prepare(key, quote).status_code, 409)
        self.assertEqual(PurchaseOrder.query.count(), 0)

    def test_future_issue_not_usable(self):
        key = self.sr()
        self.start(key)
        quote = self.quote(key).json['id']
        db.session.get(SupplierQuote, quote).issued_on = datetime.now(COMMERCIAL_ZONE).date() + timedelta(days=1)
        db.session.commit()
        self.assertEqual(self.prepare(key, quote).status_code, 400)

    def test_request_quote_requires_current_dates(self):
        key = self.sr()
        self.start(key)
        today = datetime.now(COMMERCIAL_ZONE).date()
        self.assertEqual(self.quote(key, issued_on=(today + timedelta(days=1)).isoformat()).status_code, 400)
        self.assertEqual(self.quote(key, issued_on=(today - timedelta(days=2)).isoformat(),
                                   valid_until=(today - timedelta(days=1)).isoformat()).status_code, 400)

    def test_closed_or_altered_quotes_and_today_expiry(self):
        key = self.sr()
        self.start(key)
        quote_id = self.quote(key).json['id']
        quote = db.session.get(SupplierQuote, quote_id)
        for attribute, value in [('status', 'cancelada'), ('status', 'reemplazada'),
                                 ('currency', ''), ('valid_until', quote.issued_on - timedelta(days=1))]:
            original = getattr(quote, attribute)
            setattr(quote, attribute, value)
            db.session.commit()
            self.assertEqual(self.prepare(key, quote_id).status_code, 400)
            self.assertNotIn(f'<option value="{quote_id}">CP-',
                             self.clients['asistente'].get(f'/purchases/requests/{key}').text)
            setattr(quote, attribute, original)
            db.session.commit()
        quote.valid_until = datetime.now(COMMERCIAL_ZONE).date()
        db.session.commit()
        self.assertEqual(self.prepare(key, quote_id).status_code, 200)

    def test_product_approval_changed_before_prepare(self):
        key = self.sr()
        self.start(key)
        quote = self.quote(key).json['id']
        self.product.approval_status = 'rejected'
        db.session.commit()
        self.assertEqual(self.prepare(key, quote).status_code, 400)
        self.assertEqual(PurchaseOrder.query.count(), 0)

    def test_cancel_trace(self):
        key = self.sr()
        row = self.clients['asistente'].post(f'/api/replenishment-requests/{key}/cancel', json={}).json
        self.assertIsNotNone(row.get('cancelled_by'))
        self.assertIsNotNone(row.get('cancelled_at'))
        self.assertEqual(self.start(key).status_code, 409)

    def test_create_retry_key(self):
        payload = {'idempotency_key': 'sr-audit-retry-123', 'items': [{'product_id': self.product.id, 'quantity': 2}]}
        one = self.clients['inventario'].post('/api/replenishment-requests', json=payload)
        two = self.clients['inventario'].post('/api/replenishment-requests', json=payload)
        self.assertEqual(one.status_code, 201)
        self.assertEqual(two.status_code, 201)
        self.assertEqual(one.json['id'], two.json['id'])
        self.assertEqual(ReplenishmentRequest.query.count(), 1)
        payload['items'][0]['quantity'] = 3
        self.assertEqual(self.clients['inventario'].post('/api/replenishment-requests', json=payload).status_code, 409)

    def test_order_edit_cannot_change_request_quantities(self):
        key = self.sr()
        self.start(key)
        quote = self.quote(key).json['id']
        order_id = self.prepare(key, quote).json['purchase_order_id']
        result = self.clients['asistente'].patch(f'/api/purchases/{order_id}', json={
            'supplier_id': self.supplier.id, 'supplier_quote_id': quote,
            'items': [{'product_id': self.product.id, 'quantity': 1}]})
        self.assertEqual(result.status_code, 409)

    def test_lifecycle_preserves_stock_and_processor(self):
        key = self.sr()
        start = self.start(key).json
        quote = self.quote(key).json['id']
        row = self.prepare(key, quote).json
        self.assertEqual(row['status'], 'fulfilled')
        self.assertEqual(row['status_label'], 'Convertida en OC')
        self.assertEqual(row['processed_at'], start['processed_at'])
        self.assertEqual(row['purchase_order']['status'], 'pending_approval')
        self.assertEqual(self.prepare(key, quote).status_code, 409)
        self.assertEqual(self.clients['asistente'].post(f'/api/replenishment-requests/{key}/cancel', json={}).status_code, 409)
        self.assertEqual(self.stock(), 10)
        self.assertEqual(PurchaseReception.query.count(), 0)


if __name__ == '__main__':
    import unittest
    unittest.main()
