"""Row-lock and idempotency proof for sales against the disposable MariaDB harness only.

Start first: python -B scripts/serve_sales_e2e.py (port 5024). Then run this file.
Cases:
1. stock 5, two concurrent sales of 4 -> exactly one 201 and one 409; stock 1; one Sale,
   one SaleLine, one 'salida' movement with previous 5 / new 1.
2. two concurrent POSTs with the same idempotency_key -> one sale, one deduction; both 2xx.
3. two multi-line sales in opposite product order -> no deadlock.
4. concurrent quotes/orders -> unique COT/PED numbers, none NULL, stock and movements untouched.
"""
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier

import pymysql
import requests

ORIGIN = 'http://127.0.0.1:5024'


def run():
    context = requests.get(ORIGIN + '/__sales_e2e__/context', timeout=10).json()
    assert context['database'] == 'ferreteria_conejo_e2e'
    assert 'MariaDB' in context['engine']

    def session(username):
        client = requests.Session()
        response = client.post(ORIGIN + '/api/auth/login', json={'identifier': username, 'password': context['password']}, timeout=10)
        assert response.status_code == 200, response.text
        return client

    admin = session(context['username'])
    workers = [session(context['seller_username']), session(context['seller_username'])]

    def new_product(stock):
        response = admin.post(ORIGIN + '/api/products', json={
            'code': f"{context['prefix']}-LOCK-{secrets.token_hex(3)}", 'name': 'Producto concurrencia ventas E2E',
            'category_id': context['category_id'], 'current_stock': stock, 'purchase_price': '1.00', 'sale_price': '10.00',
        }, timeout=10)
        assert response.status_code == 201, response.text
        return response.json()['id']

    def sale_body(product_id, quantity, key=None):
        return {'customer_id': context['customer_id'], 'payment_method': 'cash', 'payment_condition': 'cash',
                'idempotency_key': key or str(uuid.uuid4()), 'items': [{'product_id': product_id, 'quantity': quantity}]}

    def race(bodies):
        barrier = Barrier(len(bodies))

        def post(index):
            barrier.wait(timeout=5)
            return workers[index].post(ORIGIN + '/api/sales', json=bodies[index], timeout=30)
        with ThreadPoolExecutor(max_workers=len(bodies)) as executor:
            return [future.result() for future in [executor.submit(post, i) for i in range(len(bodies))]]

    connection = pymysql.connect(host='127.0.0.1', user='root', password='', database='ferreteria_conejo_e2e', connect_timeout=5)
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT DATABASE(), VERSION()')
            database, engine = cursor.fetchone()
            assert database == 'ferreteria_conejo_e2e'

            def db_state(product_id):
                connection.commit()  # fresh snapshot
                cursor.execute('SELECT current_stock FROM products WHERE id=%s', (product_id,))
                stock = cursor.fetchone()[0]
                cursor.execute('SELECT COUNT(*) FROM sale_lines WHERE product_id=%s', (product_id,))
                lines = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(DISTINCT sale_id) FROM sale_lines WHERE product_id=%s", (product_id,))
                sales = cursor.fetchone()[0]
                cursor.execute("SELECT movement_type, previous_stock, new_stock FROM stock_movements WHERE product_id=%s AND movement_type='salida'", (product_id,))
                exits = cursor.fetchall()
                return stock, lines, sales, exits

            # Case 1: last units.
            product_id = new_product(5)
            responses = race([sale_body(product_id, 4), sale_body(product_id, 4)])
            statuses = sorted(r.status_code for r in responses)
            assert statuses == [201, 409], [(r.status_code, r.text) for r in responses]
            conflict = next(r for r in responses if r.status_code == 409).json()['error']
            assert 'Stock insuficiente' in conflict and 'disponible 1' in conflict, conflict
            stock, lines, sales, exits = db_state(product_id)
            assert stock == 1 and lines == 1 and sales == 1, (stock, lines, sales)
            assert exits == (('salida', 5, 1),), exits
            winner = next(r for r in responses if r.status_code == 201).json()
            assert winner['sale_number'].startswith('VTA-') and winner['total'] == '40.00'
            cursor.execute('SELECT sale_number, seller_id FROM sales WHERE sale_number=%s', (winner['sale_number'],))
            row = cursor.fetchone()
            cursor.execute('SELECT id FROM users WHERE username=%s', (context['seller_username'],))
            assert row[1] == cursor.fetchone()[0], 'seller is the authenticated vendedor'
            cursor.execute("SELECT COUNT(*) FROM sales WHERE sale_number IS NULL")
            assert cursor.fetchone()[0] == 0, 'no sale left without number'

            # Case 2: same idempotency key concurrently.
            product_id = new_product(10)
            key = str(uuid.uuid4())
            responses = race([sale_body(product_id, 3, key), sale_body(product_id, 3, key)])
            statuses = sorted(r.status_code for r in responses)
            assert statuses == [200, 201], [(r.status_code, r.text) for r in responses]
            numbers = {r.json()['sale_number'] for r in responses}
            assert len(numbers) == 1, numbers
            stock, lines, sales, exits = db_state(product_id)
            assert stock == 7 and lines == 1 and sales == 1 and exits == (('salida', 10, 7),), (stock, lines, sales, exits)
            replay = workers[0].post(ORIGIN + '/api/sales', json=sale_body(product_id, 3, key), timeout=15)
            assert replay.status_code == 200 and replay.json()['sale_number'] in numbers
            assert db_state(product_id)[0] == 7

            # Case 3: two different products in opposite order -> consistent ascending lock order, no deadlock.
            a, b = new_product(10), new_product(10)
            bodies = [sale_body(a, 1), sale_body(b, 1)]
            bodies[0]['items'].append({'product_id': b, 'quantity': 1})
            bodies[1]['items'].append({'product_id': a, 'quantity': 1})
            responses = race(bodies)
            assert [r.status_code for r in responses] == [201, 201], [(r.status_code, r.text) for r in responses]
            assert db_state(a)[0] == 8 and db_state(b)[0] == 8

            # Case 4: quotes and orders numbered concurrently (NULL -> flush -> PREFIX-{id:06d}) without
            # touching stock; stock 2 does not block a quote/order of 50 units and no movement is created.
            product_id = new_product(2)
            tomorrow = (date.today() + timedelta(days=1)).isoformat()
            docs_cases = (('/api/quotes', 'quotes', 'quote_number', 'COT', {'validity_days': 15}),
                          ('/api/orders', 'orders', 'order_number', 'PED', {'estimated_delivery_date': tomorrow}))
            for path, table, column, prefix, extra in docs_cases:
                body = {'customer_id': context['customer_id'], 'items': [{'product_id': product_id, 'quantity': 50}], **extra}
                barrier = Barrier(2)

                def post_doc(index):
                    barrier.wait(timeout=5)
                    return workers[index].post(ORIGIN + path, json=body, timeout=30)
                with ThreadPoolExecutor(max_workers=2) as executor:
                    docs = [future.result() for future in [executor.submit(post_doc, i) for i in range(2)]]
                assert [r.status_code for r in docs] == [201, 201], [(r.status_code, r.text) for r in docs]
                assert all(r.json()[column] == f"{prefix}-{r.json()['id']:06d}" for r in docs)
                assert len({r.json()[column] for r in docs}) == 2, 'numbers unique'
                cursor.execute(f'SELECT COUNT(*) FROM {table} WHERE {column} IS NULL')
                assert cursor.fetchone()[0] == 0, f'no {table} left without number'
            stock, lines, sales, exits = db_state(product_id)
            assert stock == 2 and lines == 0 and sales == 0 and exits == (), 'quotes/orders never touch stock or movements'

        print(f'PASS: last-units race [201,409] stock 1 one sale/line/exit(5->1); same-key race one sale one deduction; '
              f'cross-order multi-line no deadlock; concurrent COT/PED numbering unique, no NULL, no stock writes. '
              f'Engine {engine}; database {database}.')
    finally:
        connection.rollback()
        connection.close()
        for worker in workers:
            worker.close()
        admin.close()


if __name__ == '__main__':
    run()
