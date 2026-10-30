"""Category layers: disposable synthetic-user harness, no database initialized."""
from copy import deepcopy
from pathlib import Path
import re

from test_settings_reference import app
from app.utils.inventory_reference import CATEGORIES, PRODUCTS, MOVEMENTS


def verify_category_layers():
    before = deepcopy((CATEGORIES, PRODUCTS, MOVEMENTS))
    client = app.test_client()
    html = client.get('/categories?ref=1').get_data(as_text=True)
    assert html.count('<dialog ') == 1
    for marker in ('cl-dialog', 'cl-menu', 'cl-submenu', 'cl-form-template',
                   'cl-export-template', 'maxlength="200"', 'method="dialog"'):
        assert marker in html, marker
    for index in range(6):
        assert f'id="cl-detail-{index}"' in html
        assert f'id="cl-metrics-{index}"' in html
    # Un botón por tono de la paleta aprobada (11 con 'brown' y 'navy').
    assert len(re.findall(r'data-cl-color=', html)) == 11
    assert len(re.findall(r'type="radio" name="cl-format"', html)) == 3
    assert len(re.findall(r'type="checkbox" checked', html)) == 4
    layer_markup = html[html.index('<script type="application/json" id="cl-fixture">'):]
    for button in re.findall(r'<button\b[^>]*>', layer_markup):
        assert 'type="button"' in button, button
    for route in ('categories', 'products', 'suppliers', 'inventory', 'dashboard'):
        response = client.get(f'/{route}?ref=1')
        assert response.status_code == 200, route
        assert 'Análisis predictivo' in response.get_data(as_text=True), route
        if route != 'categories':
            assert b'ix_category_layers' not in response.data, route
    # Live shares the drawer CSS but never the reference script or fixture.
    for query in ('', '?ref=0', '?ref=2'):
        normal = client.get('/categories' + query)
        assert normal.status_code == 200
        assert b'ix_category_layers.js' not in normal.data
        assert b'cl-fixture' not in normal.data and b'ix_categories_live.js' in normal.data
    app.debug = False
    assert b'ix_category_layers.js' not in client.get('/categories?ref=1').data
    app.debug = True
    assert client.get('/categories?ref=1', headers={'X-Preview-Role': 'anonymous'}).status_code == 401
    for role in ('admin', 'inventario', 'vendedor'):
        assert client.get('/categories?ref=1', headers={'X-Preview-Role': role}).status_code == 200
    for method in ('post', 'put', 'patch', 'delete'):
        assert getattr(client, method)('/categories?ref=1').status_code == 405
    assert before == (CATEGORIES, PRODUCTS, MOVEMENTS)
    assert 'sqlalchemy' not in app.extensions
    script = (Path(__file__).resolve().parents[1] / 'app/static/js/ix_category_layers.js').read_text(encoding='utf-8')
    for forbidden in ('fetch(', 'XMLHttpRequest', 'sendBeacon', 'localStorage',
                      'sessionStorage', 'indexedDB', 'createObjectURL', 'WebSocket'):
        assert forbidden not in script, forbidden
    print('PASS: category templates, palette, export controls, five reference routes, '
          'sidebar, normal/debug isolation, auth/roles, rejected mutation methods, '
          'unchanged fixtures; no database initialized')


if __name__ == '__main__':
    verify_category_layers()
