"""Session-authenticated purchase/quote writes: real CSRF, isolated SQLite."""
from datetime import datetime, timedelta

from approval_test_support import LinkedSupplierCase
from app.models.purchase import PurchaseOrder, PurchaseReception
from app.models.supplier_quote import SupplierQuote
from app.services.inventory_service import COMMERCIAL_ZONE


class PurchaseCsrf(LinkedSupplierCase):
    def quote(self):
        today = datetime.now(COMMERCIAL_ZONE).date()
        response = self.clients['asistente'].post('/api/supplier-quotes', json={
            'supplier_id': self.supplier.id, 'currency': 'VES', 'issued_on': today.isoformat(),
            'valid_until': (today + timedelta(days=3)).isoformat(),
            'items': [{'product_id': self.product.id, 'quantity': 2, 'unit_price': '8.50'}]})
        self.assertEqual(response.status_code, 201)
        return response.json

    def order(self):
        quote = self.quote()
        response = self.clients['asistente'].post('/api/purchases', json={
            'supplier_id': self.supplier.id, 'supplier_quote_id': quote['id'],
            'items': [{'product_id': self.product.id, 'quantity': 2}]})
        self.assertEqual(response.status_code, 201)
        return response.json

    def writers(self):
        for rule in self.app.url_map.iter_rules():
            if rule.rule.startswith(('/api/purchases', '/api/supplier-quotes')):
                for method in rule.methods & {'POST', 'PUT', 'PATCH', 'DELETE'}:
                    yield rule.rule.replace('<int:order_id>', '999').replace('<int:quote_id>', '999'), method

    def test_every_write_rejects_absent_invalid_and_foreign_session_csrf(self):
        for path, method in self.writers():
            actor = 'admin' if any(word in path for word in ('approve', 'reject')) or (path.startswith('/api/purchases/') and path.endswith('/cancel')) else 'inventario' if path.endswith('/receptions') else 'asistente'
            client = self.clients[actor]
            foreign = self.clients['asistente' if actor != 'asistente' else 'admin'].environ_base['HTTP_X_CSRFTOKEN']
            for token in ('', 'invalid', foreign):
                with self.subTest(path=path, method=method, token=token[:7]):
                    response = client.open(path, method=method, json={}, headers={'X-CSRFToken': token})
                    self.assertEqual(response.status_code, 400, response.json)
                    self.assertEqual(response.json['error'], 'Token CSRF inválido o ausente.')
        self.assertEqual(PurchaseOrder.query.count(), 0)
        self.assertEqual(SupplierQuote.query.count(), 0)
        self.assertEqual(PurchaseReception.query.count(), 0)
        self.assertEqual(self.stock(), 10)

    def test_valid_csrf_quote_cancel_patch_duplicate_and_decisions(self):
        quote = self.quote()
        self.assertEqual(self.clients['asistente'].post(f"/api/supplier-quotes/{quote['id']}/cancel", json={}).status_code, 200)
        order = self.order()
        client = self.clients['asistente']
        self.assertEqual(client.patch(f"/api/purchases/{order['id']}", json={'notes': 'Cambio legítimo'}).status_code, 200)
        duplicate = client.post(f"/api/purchases/{order['id']}/duplicate", json={})
        self.assertEqual(duplicate.status_code, 201)
        self.assertEqual(self.clients['admin'].post(f"/api/purchases/{order['id']}/cancel", json={'reason': 'Prueba'}).status_code, 200)
        self.assertEqual(self.clients['admin'].post(f"/api/purchases/{duplicate.json['id']}/reject", json={'reason': 'Prueba'}).status_code, 200)
        approved = self.order()
        self.assertEqual(self.clients['admin'].post(f"/api/purchases/{approved['id']}/approve", json={}).status_code, 200)

    def test_valid_csrf_closure_reject_approve_and_receive(self):
        order = self.order()
        path = f"/api/purchases/{order['id']}"
        admin, assistant = self.clients['admin'], self.clients['asistente']
        self.assertEqual(admin.post(path + '/approve', json={}).status_code, 200)
        self.assertEqual(assistant.post(path + '/closure-requests', json={'kind': 'unavailable', 'reason': 'Prueba'}).status_code, 200)
        self.assertEqual(admin.post(path + '/closure-requests/reject', json={'reason': 'Prueba'}).status_code, 200)
        self.assertEqual(assistant.post(path + '/closure-requests', json={'kind': 'unavailable', 'reason': 'Prueba'}).status_code, 200)
        self.assertEqual(admin.post(path + '/closure-requests/approve', json={}).status_code, 200)
        receivable = self.order()
        path = f"/api/purchases/{receivable['id']}"
        self.assertEqual(admin.post(path + '/approve', json={}).status_code, 200)
        self.assertEqual(self.clients['inventario'].post(path + '/receptions', json={
            'received_at': datetime.now(COMMERCIAL_ZONE).date().isoformat(),
            'items': [{'line_id': receivable['items'][0]['id'], 'quantity': 1}]}).status_code, 200)
        self.assertEqual(self.stock(), 11)

    def test_valid_csrf_keeps_supplier_creation_business_restriction(self):
        response = self.clients['asistente'].post('/api/purchases/suppliers', json={})
        self.assertEqual(response.status_code, 403)
        self.assertIn('No se crea desde la orden de compra.', response.json['error'])

    def test_get_and_rbac_independent_of_csrf_and_bearer_does_not_authenticate(self):
        row = self.order()
        client = self.clients['asistente']
        self.assertEqual(client.get(f"/api/purchases/{row['id']}", headers={'X-CSRFToken': ''}).status_code, 200)
        for actor, action in (('inventario', 'approve'), ('asistente', 'approve')):
            self.assertEqual(self.clients[actor].post(f"/api/purchases/{row['id']}/{action}", json={}, headers={'X-CSRFToken': ''}).status_code, 403)
        self.assertEqual(self.app.test_client().post('/api/purchases', json={}, headers={'Authorization': 'Bearer fake-independent-token'}).status_code, 401)
        for method in ('PUT', 'DELETE'):
            self.assertEqual(client.open(f"/api/purchases/{row['id']}", method=method, json={}, headers={'X-CSRFToken': ''}).status_code, 405)

    def test_browser_pages_supply_tokens_to_both_purchase_clients(self):
        for path, context_id in (('/purchases', 'po-context'), ('/purchases/receptions', 'rc-context')):
            html = self.clients['asistente'].get(path).text
            self.assertIn('"csrf":', html)
            self.assertIn(context_id, html)


if __name__ == '__main__':
    import unittest
    unittest.main(verbosity=2)
