"""SR CSRF regression checks in disposable SQLite; no MySQL or delivery."""
from datetime import datetime, timedelta

from approval_test_support import LinkedSupplierCase
from app.models.replenishment_request import ReplenishmentRequest
from app.models.purchase import PurchaseOrder
from app.services.inventory_service import COMMERCIAL_ZONE


class RequestCsrf(LinkedSupplierCase):
    def create_request(self):
        response = self.clients['inventario'].post('/api/replenishment-requests', json={
            'items': [{'product_id': self.product.id, 'quantity': 2}]})
        self.assertEqual(response.status_code, 201)
        return response.json['id']

    def test_direct_create_requires_valid_token(self):
        for token in ('', 'invalid'):
            response = self.clients['inventario'].post('/api/replenishment-requests', json={
                'items': [{'product_id': self.product.id, 'quantity': 2}]}, headers={'X-CSRFToken': token})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(ReplenishmentRequest.query.count(), 0)
        self.create_request()

    def test_all_actions_reject_absent_and_invalid_token_before_changes(self):
        key = self.create_request()
        for action in ('start', 'cancel', 'quotes', 'prepare'):
            for token in ('', 'invalid'):
                response = self.clients['asistente'].post(f'/api/replenishment-requests/{key}/{action}',
                    json={}, headers={'X-CSRFToken': token})
                self.assertEqual(response.status_code, 400)
        row = self.clients['asistente'].get(f'/api/replenishment-requests/{key}').json
        self.assertEqual(row['status'], 'open')
        self.assertIsNone(row['processed_by'])
        self.assertIsNone(row['purchase_order_id'])

    def test_valid_token_complete_preparation_and_cancellation(self):
        key = self.create_request()
        path = f'/api/replenishment-requests/{key}'
        self.assertEqual(self.clients['asistente'].post(path + '/start', json={}).status_code, 200)
        today = datetime.now(COMMERCIAL_ZONE).date()
        quote = self.clients['asistente'].post(path + '/quotes', json={
            'supplier_id': self.supplier.id, 'currency': 'VES', 'issued_on': today.isoformat(),
            'valid_until': (today + timedelta(days=2)).isoformat(),
            'items': [{'product_id': self.product.id, 'unit_price': '2.00'}]})
        self.assertEqual(quote.status_code, 201)
        prepared = self.clients['asistente'].post(path + '/prepare', json={'supplier_quote_id': quote.json['id']})
        self.assertEqual(prepared.status_code, 200)
        self.assertEqual(prepared.json['status'], 'fulfilled')
        self.assertEqual(PurchaseOrder.query.count(), 1)
        another = self.create_request()
        cancelled = self.clients['asistente'].post(f'/api/replenishment-requests/{another}/cancel', json={})
        self.assertEqual(cancelled.status_code, 200)
        self.assertIsNotNone(cancelled.json['cancelled_by'])
        self.assertEqual(self.stock(), 10)

    def test_template_token_and_rbac_remain_effective(self):
        key = self.create_request()
        html = self.clients['asistente'].get(f'/purchases/requests/{key}').text
        self.assertIn('data-csrf="', html)
        response = self.clients['inventario'].post(f'/api/replenishment-requests/{key}/start', json={},
                                                 headers={'X-CSRFToken': 'invalid'})
        self.assertEqual(response.status_code, 403)


if __name__ == '__main__':
    import unittest
    unittest.main(verbosity=2)
