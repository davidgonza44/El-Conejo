"""Quote layers: synthetic-user reference rendering, no database initialized."""
from copy import deepcopy
from pathlib import Path

from test_settings_reference import app
from app.utils.sales_reference import QUOTES


def verify_quote_reference():
    before = deepcopy(QUOTES)
    client = app.test_client()
    html = client.get('/sales/quotes?ref=1').get_data(as_text=True)
    for marker in ('ql-drawer', 'ql-dates', 'ql-status', 'ql-more',
                   'ql-quote-date', 'ql-filter-from', 'ql-filter-to',
                   'ql-single-calendar', 'Productos cotizados',
                   'Resumen de la cotización', 'Guardar cotización'):
        assert marker in html, marker
    assert html.count('data-ql-date=') == 3
    assert html.count('data-ql-quick=') == 6
    assert 'sqlalchemy' not in app.extensions
    for route in ('/sales/quotes', '/sales/new', '/sales/orders',
                  '/sales/customers', '/sales', '/products', '/inventory',
                  '/categories', '/suppliers'):
        response = client.get(route + '?ref=1')
        assert response.status_code == 200, (route, response.status_code)
        assert 'Análisis predictivo' in response.get_data(as_text=True), route
        if route != '/sales/quotes':
            assert b'ix_quote_layers' not in response.data, route
    for query in ('', '?ref=0', '?ref=2'):
        normal = client.get('/sales/quotes' + query)
        assert normal.status_code == 200
        assert b'ix_quote_layers' not in normal.data
    app.debug = False
    assert b'ix_quote_layers' not in client.get('/sales/quotes?ref=1').data
    app.debug = True
    assert client.get('/sales/quotes?ref=1', headers={'X-Preview-Role': 'anonymous'}).status_code == 401
    for method in ('post', 'put', 'patch', 'delete'):
        assert getattr(client, method)('/sales/quotes?ref=1').status_code == 405
    assert before == QUOTES
    script = (Path(__file__).resolve().parents[1] / 'app/static/js/ix_quote_layers.js').read_text(encoding='utf-8')
    for forbidden in ('fetch(', 'XMLHttpRequest', 'sendBeacon', 'localStorage',
                      'sessionStorage', 'indexedDB', 'WebSocket'):
        assert forbidden not in script, forbidden
    print('PASS: quote layers, three visual date fields, six quick ranges, nine reference routes, '
          'sidebar, normal/debug isolation, auth, rejected mutations, unchanged fixtures; no database initialized')


if __name__ == '__main__':
    verify_quote_reference()
