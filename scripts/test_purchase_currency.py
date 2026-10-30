"""Currency and historical presentation regressions; isolated SQLite only."""
from datetime import datetime, timedelta
from decimal import Decimal

from approval_test_support import LinkedSupplierCase
from app.extensions import db
from app.models import StockMovement
from app.models.purchase import PurchaseOrder, PurchaseReception
from app.models.replenishment_request import ReplenishmentRequest
from app.models.supplier_quote import SupplierQuote
from app.services.inventory_service import COMMERCIAL_ZONE
from app.utils.purchase_currency import format_amount


class PurchaseCurrency(LinkedSupplierCase):
    def quote_data(self, currency):
        today = datetime.now(COMMERCIAL_ZONE).date()
        return {'supplier_id': self.supplier.id, 'currency': currency, 'issued_on': today.isoformat(),
                'valid_until': (today + timedelta(days=3)).isoformat(),
                'items': [{'product_id': self.product.id, 'quantity': 2, 'unit_price': '8.50'}]}

    def order(self, currency):
        client = self.clients['asistente']
        quote = client.post('/api/supplier-quotes', json=self.quote_data(currency))
        self.assertEqual(quote.status_code, 201, quote.json)
        payload = {'supplier_id': self.supplier.id, 'supplier_quote_id': quote.json['id'],
                   'items': [{'product_id': self.product.id, 'quantity': 2}]}
        created = client.post('/api/purchases', json=payload)
        self.assertEqual(created.status_code, 201, created.json)
        return created.json, payload

    def test_usd_and_ves_details_lists_print_and_export(self):
        for currency, symbol in (('USD', '$'), ('VES', 'Bs.')):
            with self.subTest(currency=currency):
                row, _ = self.order(currency)
                client = self.clients['asistente']
                detail = client.get(f"/api/purchases/{row['id']}").json
                self.assertEqual(detail['currency'], currency)
                self.assertEqual((detail['subtotal'], detail['total'], detail['pending_amount']), ('17.00',) * 3)
                printed = client.get(f"/purchases/{row['id']}/print").text
                self.assertIn(f'Total: {symbol} 17.00', printed)
                self.assertIn(f'{symbol} 8.50', printed)
                self.assertNotIn(('Bs.' if currency == 'USD' else '$') + ' 17.00', printed)
                params = {'search': row['order_number'], 'start': '', 'end': ''}
                listing = client.get('/api/purchases', query_string=params).json
                self.assertEqual(listing['items'][0]['currency'], currency)
                self.assertIn(f'{symbol} 17.00', client.get('/purchases', query_string=params).text)
                exported = client.get('/api/purchases/export.csv', query_string=params).text
                self.assertIn(currency, exported)
                self.assertIn(f'{symbol} 17.00', exported)
        self.assertEqual(self.stock(), 10)
        self.assertEqual(StockMovement.query.count(), 0)

    def test_invalid_quotes_rejected_without_normalizing_or_writing(self):
        client = self.clients['asistente']
        for currency in ('EUR', 'GBP', 'usd', 'ves', ' USD', 'VES ', '', None, 123, True, {}, []):
            with self.subTest(currency=currency):
                result = client.post('/api/supplier-quotes', json=self.quote_data(currency))
                self.assertEqual(result.status_code, 400)
                self.assertEqual(result.json['error'], 'La moneda debe ser USD o VES.')
        self.assertEqual(SupplierQuote.query.count(), 0)

    def test_order_cannot_override_currency_on_create_or_edit(self):
        for currency in ('USD', 'VES'):
            row, payload = self.order(currency)
            for injected in ('USD', 'VES', 'EUR', None):
                self.assertEqual(self.clients['asistente'].post('/api/purchases', json={**payload, 'currency': injected}).status_code, 400)
                self.assertEqual(self.clients['asistente'].patch(f"/api/purchases/{row['id']}", json={'currency': injected}).status_code, 400)
            self.assertEqual(self.clients['asistente'].get(f"/api/purchases/{row['id']}").json['currency'], currency)
        self.assertEqual(PurchaseOrder.query.count(), 2)

    def test_mixed_currency_totals_are_separated_and_inventory_has_no_amounts(self):
        usd, _ = self.order('USD')
        self.order('VES')
        result = self.clients['asistente'].get('/api/purchases', query_string={'start': '', 'end': ''}).json
        self.assertEqual(result['kpis']['totals_by_currency'], [
            {'currency': 'USD', 'amount': '17.00'}, {'currency': 'VES', 'amount': '17.00'}])
        html = self.clients['asistente'].get('/purchases?start=&end=').text
        # Cada moneda en su propia línea del KPI, sin convertir ni sumar.
        self.assertRegex(html, r'USD</span><strong>\$ 17\.00</strong></li><li><span class="ds-currency">VES</span><strong>Bs\. 17\.00</strong>')
        self.assertNotIn('$ 34.00', html)
        supplier = self.clients['asistente'].get(f'/api/suppliers/{self.supplier.id}').json
        self.assertEqual(supplier['totals_by_currency'], result['kpis']['totals_by_currency'])
        import csv
        from io import StringIO
        exported = self.clients['asistente'].get('/api/suppliers/export?format=csv')
        record = next(csv.DictReader(StringIO(exported.data.decode('utf-8-sig'))))
        self.assertEqual((record['Compras USD'], record['Compras VES']), ('$ 17.00', 'Bs. 17.00'))
        inventory = self.clients['inventario'].get(f"/api/purchases/{usd['id']}").json
        for key in ('total', 'subtotal', 'pending_amount'):
            self.assertNotIn(key, inventory)
        self.assertNotIn('unit_cost', inventory['items'][0])
        printed = self.clients['inventario'].get(f"/purchases/{usd['id']}/print").text
        self.assertNotIn('$ 17.00', printed)
        self.assertNotIn('Costo unitario', printed)

    def test_persisted_ves_survives_removal_of_quote_without_conversion(self):
        row, _ = self.order('VES')
        order = db.session.get(PurchaseOrder, row['id'])
        order.supplier_quote_id = None
        db.session.commit()
        client = self.clients['asistente']
        self.assertEqual(client.get(f"/api/purchases/{row['id']}").json['currency'], 'VES')
        self.assertIn('Bs. 17.00', client.get(f"/purchases/{row['id']}/print").text)
        self.assertEqual(order.total, 17)
        self.assertEqual(format_amount('17.00', None), 'Moneda no registrada · 17.00')

    def test_jinja_singular_plural_and_reference_preserves_denomination(self):
        template = self.app.jinja_env.get_template('purchases/_money.html').module
        for count, text in ((0, '0 renglones'), (1, '1 renglón'), (3, '3 renglones')):
            self.assertEqual(template.count(count, 'renglón', 'renglones'), text)
        for path in ('/purchases?ref=1', '/purchases/receptions?ref=1', '/suppliers?ref=1'):
            response = self.clients['asistente'].get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('$ ', response.text)
            self.assertNotIn('Moneda no registrada', response.text)

    def test_unsupported_historical_quote_preserved_but_not_usable(self):
        row, payload = self.order('VES')
        quote = db.session.get(SupplierQuote, payload['supplier_quote_id'])
        quote.currency = 'EUR'
        db.session.commit()
        client = self.clients['asistente']
        self.assertEqual(client.post('/api/purchases', json=payload).status_code, 400)
        options = client.get(f'/api/purchases/options?supplier_id={self.supplier.id}').json
        self.assertEqual(options['quotes'], [])
        self.assertIn('Bs. 17.00', client.get(f"/purchases/{row['id']}/print").text)
        self.assertEqual(db.session.get(PurchaseOrder, row['id']).currency, 'VES')
        self.assertEqual(db.session.get(SupplierQuote, quote.id).currency, 'EUR')

    def test_required_currency_rejects_missing_null_invalid_and_lowercase_without_default(self):
        from sqlalchemy import inspect, text
        from sqlalchemy.exc import IntegrityError
        row, _ = self.order('VES')
        column = next(c for c in inspect(db.engine).get_columns('purchase_orders') if c['name'] == 'currency')
        self.assertFalse(column['nullable'])
        self.assertIsNone(column['default'])
        for currency in (None, '', 'EUR', 'usd', 'ves', ' USD', 'VES '):
            with self.subTest(currency=currency), self.assertRaises(IntegrityError):
                with db.session.begin_nested():
                    db.session.execute(text('UPDATE purchase_orders SET currency=:currency WHERE id=:id'),
                                       {'currency': currency, 'id': row['id']})
        with self.assertRaises(IntegrityError):
            with db.session.begin_nested():
                db.session.add(PurchaseOrder(supplier_id=self.supplier.id,
                                            created_by_user_id=self.users['asistente'].id))
                db.session.flush()
        db.session.expire_all()
        preserved = db.session.get(PurchaseOrder, row['id'])
        self.assertEqual((preserved.currency, preserved.total, preserved.lines[0].quantity_ordered), ('VES', 17, 2))

    def test_pending_edit_cannot_relabel_existing_prices_using_another_currency(self):
        row, _ = self.order('VES')
        quote = self.clients['asistente'].post('/api/supplier-quotes', json=self.quote_data('USD')).json
        result = self.clients['asistente'].patch(f"/api/purchases/{row['id']}", json={
            'supplier_quote_id': quote['id'], 'items': [{'product_id': self.product.id, 'quantity': 2}]})
        self.assertEqual(result.status_code, 400)
        preserved = self.clients['asistente'].get(f"/api/purchases/{row['id']}").json
        self.assertEqual((preserved['currency'], preserved['total'], preserved['supplier_quote_id']),
                         ('VES', '17.00', row['supplier_quote_id']))

    def test_sr_cycle_inherits_currency_and_records_processor_at_start(self):
        for currency in ('USD', 'VES'):
            sr = self.clients['inventario'].post('/api/replenishment-requests', json={
                'items': [{'product_id': self.product.id, 'quantity': 2}]}).json
            path = f"/api/replenishment-requests/{sr['id']}"
            started = self.clients['asistente'].post(path + '/start', json={})
            self.assertEqual(started.status_code, 200)
            self.assertEqual(started.json['processed_by']['id'], self.users['asistente'].id)
            self.assertIsNotNone(started.json['processed_at'])
            html = self.clients['asistente'].get(f"/purchases/requests/{sr['id']}").text
            self.assertIn('<select name="currency" required>', html)
            self.assertIn('USD ($)', html)
            self.assertIn('VES (Bs.)', html)
            data = self.quote_data(currency)
            data['items'] = [{'product_id': self.product.id, 'unit_price': '8.50'}]
            self.assertEqual(self.clients['asistente'].post(path + '/quotes', json={**data, 'currency': 'EUR'}).status_code, 400)
            quote = self.clients['asistente'].post(path + '/quotes', json=data)
            self.assertEqual(quote.status_code, 201)
            prepared = self.clients['asistente'].post(path + '/prepare', json={'supplier_quote_id': quote.json['id']})
            self.assertEqual(prepared.status_code, 200)
            oid = prepared.json['purchase_order_id']
            self.assertEqual(self.clients['admin'].post(f'/api/purchases/{oid}/approve', json={}).status_code, 200)
            self.assertEqual(self.clients['asistente'].get(f'/api/purchases/{oid}').json['currency'], currency)
            self.assertEqual(prepared.json['processed_at'], started.json['processed_at'])
        self.assertEqual(self.stock(), 10)
        self.assertEqual(PurchaseReception.query.count(), 0)

    def test_historical_processing_nulls_remain_and_display_no_registrado(self):
        sr = self.clients['inventario'].post('/api/replenishment-requests', json={
            'items': [{'product_id': self.product.id, 'quantity': 1}]}).json
        row = db.session.get(ReplenishmentRequest, sr['id'])
        row.status = 'processing'
        db.session.commit()
        html = self.clients['asistente'].get(f"/purchases/requests/{sr['id']}").text
        self.assertIn('<dt>Procesada por</dt><dd>No registrado</dd>', html)
        self.assertIn('<dt>Fecha de proceso</dt><dd>No registrado</dd>', html)
        db.session.refresh(row)
        self.assertIsNone(row.processed_by_user_id)
        self.assertIsNone(row.processed_at)

    def test_receipt_symbols_exports_pdf_and_inventory_privacy(self):
        from unittest.mock import patch
        from io import BytesIO
        for currency, symbol in (('USD', '$'), ('VES', 'Bs.')):
            order, _ = self.order(currency)
            self.assertEqual(self.clients['admin'].post(f"/api/purchases/{order['id']}/approve", json={}).status_code, 200)
            received = self.clients['inventario'].post(f"/api/purchases/{order['id']}/receptions", json={
                'received_at': datetime.now(COMMERCIAL_ZONE).date().isoformat(),
                'items': [{'line_id': order['items'][0]['id'], 'quantity': 2}]})
            self.assertEqual(received.status_code, 200)
            rid = received.json['receptions'][0]['id']
            client = self.clients['asistente']
            self.assertIn(f'Total recibido: {symbol} 17.00', client.get(f'/purchases/receptions/{rid}/print').text)
            self.assertIn(f'{symbol} 17.00', client.get('/api/purchases/receptions/export?format=csv').text)
            with patch('app.services.export_files.pdf_report', return_value=BytesIO(b'%PDF-fixture')) as pdf:
                self.assertEqual(client.get(f'/purchases/receptions/{rid}/pdf').status_code, 200)
                self.assertIn('Total recibido: ' + symbol + ' 17.00', pdf.call_args.args[1])
                self.assertEqual(pdf.call_args.args[2][0]['unit_cost'], symbol + ' 8.50')
            inventory = self.clients['inventario']
            self.assertNotIn(f'{symbol} 17.00', inventory.get(f'/purchases/receptions/{rid}/print').text)
            self.assertNotIn('Costo unitario', inventory.get(f'/purchases/receptions/{rid}/print').text)
            with patch('app.services.export_files.pdf_report', return_value=BytesIO(b'%PDF-fixture')) as pdf:
                self.assertEqual(inventory.get(f'/purchases/receptions/{rid}/pdf').status_code, 200)
                self.assertNotIn('unit_cost', [c[0] for c in pdf.call_args.args[3]])


class PurchaseCurrencyComparison(LinkedSupplierCase):
    """Variación por moneda contra la ventana anterior de igual duración (11-20 vs. 1-10 de marzo)."""
    WINDOW = {'start': '2026-03-11', 'end': '2026-03-20'}

    def placed(self, currency, when, total='17.00', status='approved'):
        # Fila directa en la base SQLite desechable: las órdenes reales nacen de una solicitud de reposición.
        order = PurchaseOrder(supplier_id=self.supplier.id, created_by_user_id=self.users['asistente'].id,
                              ordered_at=when, status=status, subtotal=Decimal(total), total=Decimal(total), currency=currency)
        db.session.add(order)
        db.session.commit()
        return order

    def kpis(self, client='asistente', **params):
        response = self.clients[client].get('/api/purchases', query_string={**self.WINDOW, **params})
        self.assertEqual(response.status_code, 200)
        return response.json['kpis']

    def test_usd_and_ves_compared_independently_without_conversion(self):
        self.placed('USD', datetime(2026, 3, 15, 16))
        self.placed('VES', datetime(2026, 3, 16, 16))
        self.placed('VES', datetime(2026, 3, 5, 16), total='10.00')
        self.placed('USD', datetime(2026, 3, 6, 16), total='50.00', status='rejected')
        self.placed('USD', datetime(2026, 2, 27, 16), total='40.00')
        k = self.kpis()
        self.assertEqual(k['totals_by_currency'], [{'currency': 'USD', 'amount': '17.00'}, {'currency': 'VES', 'amount': '17.00'}])
        self.assertEqual(k['comparison_by_currency'], [
            {'currency': 'USD', 'previous_amount': None, 'change_percent': None, 'reason': 'no_previous',
             'text': 'Sin compras en el período anterior'},
            {'currency': 'VES', 'previous_amount': '10.00', 'change_percent': '+70.0', 'reason': 'ok',
             'text': '+70,0 % vs. período anterior'}])
        self.assertIsNone(k['comparison'])
        html = self.clients['asistente'].get('/purchases', query_string=self.WINDOW).text
        self.assertRegex(html, r'USD</span><strong>\$ 17\.00</strong><span class="ds-money-change"[^>]*>Sin compras en el período anterior</span></li>')
        self.assertRegex(html, r'VES</span><strong>Bs\. 17\.00</strong><span class="ds-money-change"[^>]*>\+70,0 % vs\. período anterior</span></li>')
        self.assertNotIn('Sin datos comparables', html)
        for combined in ('$ 34.00', 'Bs. 34.00', '$ 27.00', 'Bs. 27.00'):
            self.assertNotIn(combined, html)

    def test_previous_zero_amount_is_not_divided(self):
        self.placed('VES', datetime(2026, 3, 15, 16))
        self.placed('VES', datetime(2026, 3, 5, 16), total='0.00')
        self.placed('USD', datetime(2026, 3, 16, 16))
        self.placed('USD', datetime(2026, 3, 6, 16), total='20.00')
        k = self.kpis()
        self.assertEqual([(r['currency'], r['reason'], r['previous_amount'], r['change_percent']) for r in k['comparison_by_currency']],
                         [('USD', 'ok', '20.00', '-15.0'), ('VES', 'previous_zero', '0.00', None)])
        self.assertEqual(k['comparison_by_currency'][1]['text'], 'Período anterior con importe cero; sin variación porcentual')
        self.assertEqual(k['comparison_by_currency'][0]['text'], '-15,0 % vs. período anterior')
        self.assertIsNone(k['comparison'])
        html = self.clients['asistente'].get('/purchases', query_string=self.WINDOW).text
        self.assertIn('>Período anterior con importe cero; sin variación porcentual</span>', html)
        # Período sin compras actuales: no hay renglones por moneda que comparar.
        self.assertEqual(self.kpis(start='2026-03-21', end='2026-03-30')['comparison_by_currency'], [])

    def test_legacy_comparison_value_unchanged_for_single_currency(self):
        self.placed('VES', datetime(2026, 3, 15, 16))
        self.placed('VES', datetime(2026, 3, 5, 16), total='10.00')
        k = self.kpis()
        self.assertEqual(k['comparison'], '+70.0%')
        self.assertEqual(k['comparison_by_currency'][0]['text'], '+70,0 % vs. período anterior')

    def test_cost_permission_required_for_comparisons(self):
        self.placed('USD', datetime(2026, 3, 15, 16))
        self.placed('USD', datetime(2026, 3, 5, 16), total='10.00')
        k = self.kpis('inventario')
        self.assertEqual((k['totals_by_currency'], k['comparison'], k['comparison_by_currency']), ([], None, []))
        html = self.clients['inventario'].get('/purchases', query_string=self.WINDOW).text
        for leaked in ('vs. período anterior', 'Sin compras en el período anterior', '+70,0', '$ 17.00'):
            self.assertNotIn(leaked, html)
        self.assertEqual(self.kpis()['comparison_by_currency'][0]['change_percent'], '+70.0')

    def test_date_filters_define_both_windows_and_cleared_dates_do_not_compare(self):
        self.placed('VES', datetime(2026, 3, 15, 16))
        self.placed('VES', datetime(2026, 3, 5, 16), total='10.00')
        cleared = self.kpis(start='', end='')
        self.assertEqual(cleared['comparison_by_currency'], [])
        self.assertIn('Sin datos comparables', self.clients['asistente'].get('/purchases?start=&end=').text)
        # 15-15 compara contra 14-14: sin compras previas aunque existan antes de esa ventana.
        self.assertEqual(self.kpis(start='2026-03-15', end='2026-03-15')['comparison_by_currency'][0]['reason'], 'no_previous')
        # 6-15 compara contra 26 feb-5 mar (diez días), que incluye la compra del día 5.
        wide = self.kpis(start='2026-03-06', end='2026-03-15')['comparison_by_currency'][0]
        self.assertEqual((wide['previous_amount'], wide['change_percent']), ('10.00', '+70.0'))
        db.session.expire_all()
        self.assertEqual(sorted(str(o.total) for o in PurchaseOrder.query.all()), ['10.00', '17.00'])


if __name__ == '__main__':
    import unittest
    unittest.main(verbosity=2)
