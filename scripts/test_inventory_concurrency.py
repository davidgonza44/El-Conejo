"""Focused row-lock test against the guarded, disposable MariaDB E2E harness only."""
import secrets
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Barrier

import pymysql
import requests

ORIGIN = 'http://127.0.0.1:5023'


def run():
    context = requests.get(ORIGIN + '/__inventory_e2e__/context', timeout=10).json()
    assert context['database'] == 'ferreteria_conejo_e2e'
    assert 'MariaDB' in context['engine']

    def session():
        client = requests.Session()
        response = client.post(ORIGIN + '/api/auth/login', json={
            'identifier':context['username'], 'password':'DisposableInventoryOnly123!',
        }, timeout=10)
        assert response.status_code == 200
        return client

    client = session()
    source = client.get(ORIGIN + '/api/products/' + str(context['product_id']), timeout=10).json()
    response = client.post(ORIGIN + '/api/products', json={
        'code':'MV-LOCK-' + secrets.token_hex(4), 'name':'Producto concurrencia E2E',
        'category_id':source['category_id'], 'current_stock':10,
    }, timeout=10)
    assert response.status_code == 201
    product_id = response.json()['id']
    workers = [session(), session()]
    barrier = Barrier(2)

    def exit_stock(index):
        barrier.wait(timeout=5)
        return workers[index].post(ORIGIN + '/api/inventory/exit', json={
            'product_id':product_id, 'quantity':7, 'reason':'Concurrencia E2E',
        }, timeout=15).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(exit_stock, index) for index in range(2)]
        assert sorted(future.result() for future in futures) == [201, 409]
    product = client.get(ORIGIN + '/api/products/' + str(product_id), timeout=10).json()
    assert product['current_stock'] == 3
    history = client.get(ORIGIN + f'/api/inventory/products/{product_id}/movements', timeout=10).json()
    assert history['count'] == 2

    connection = pymysql.connect(host='127.0.0.1', user='root', password='',
                                 database='ferreteria_conejo_e2e', connect_timeout=5)
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT DATABASE(), VERSION()')
            database, engine = cursor.fetchone()
            assert database == 'ferreteria_conejo_e2e'
            cursor.execute('SELECT current_stock FROM products WHERE id=%s FOR UPDATE', (product_id,))
            assert cursor.fetchone()[0] == 3
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(workers[0].post, ORIGIN + '/api/inventory/entry',
                                     json={'product_id':product_id, 'quantity':2, 'reason':'Bloqueo E2E'}, timeout=15)
            try:
                future.result(timeout=0.5)
                raise AssertionError('Request completed while another connection held the row lock')
            except TimeoutError:
                pass
            finally:
                connection.rollback()
            assert future.result(timeout=10).status_code == 201
        assert client.get(ORIGIN + '/api/products/' + str(product_id), timeout=10).json()['current_stock'] == 5
        print(f'PASS: concurrent exits [201,409], stock 3, no extra movement; held row lock blocks entry until release; stock 5. Engine {engine}; database {database}.')
    finally:
        connection.rollback()
        connection.close()
        for worker in workers:
            worker.close()
        client.close()


if __name__ == '__main__':
    run()
