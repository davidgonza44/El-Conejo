"""Supplier preview checks using the existing synthetic harness, with no database."""
from copy import deepcopy
from pathlib import Path
from test_settings_reference import app
from app.utils.inventory_reference import SUPPLIERS


def verify():
    before = deepcopy(SUPPLIERS)
    client = app.test_client()
    html = client.get('/suppliers?ref=1').get_data(as_text=True)
    assert html.count('<dialog ') == 3
    assert html.count(' required') == 7
    assert 'method="dialog"' in html and 'maxlength="300"' in html
    for query in ('', '?ref=0', '?ref=2'):
        assert b'ix_supplier_layers' not in client.get('/suppliers' + query).data
    app.debug = False
    assert b'ix_supplier_layers' not in client.get('/suppliers?ref=1').data
    app.debug = True
    assert client.get('/suppliers?ref=1', headers={'X-Preview-Role': 'anonymous'}).status_code == 401
    for method in ('post', 'put', 'patch', 'delete'):
        assert getattr(client, method)('/suppliers?ref=1').status_code == 405
    script = (Path(__file__).resolve().parents[1] / 'app/static/js/ix_supplier_layers.js').read_text(encoding='utf-8')
    for forbidden in ('fetch(', 'XMLHttpRequest', 'sendBeacon', 'localStorage', 'sessionStorage', 'indexedDB', 'WebSocket'):
        assert forbidden not in script
    assert SUPPLIERS == before
    assert 'sqlalchemy' not in app.extensions
    print('PASS: seven required fields, dialog-only form, ref/debug isolation, authentication, HTTP mutation rejection, unchanged fixture, no database initialized.')


if __name__ == '__main__':
    verify()
