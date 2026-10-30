"""Read-only route and fixture isolation checks; no database is initialized."""
from test_settings_reference import app

client = app.test_client()
tabs = ('summary', 'products', 'customers', 'categories', 'payments')
for tab in tabs:
    route = '/reports/sales?tab=' + tab
    response = client.get(route + '&ref=1')
    assert response.status_code == 200
    assert response.data.count(b'aria-label="Secciones del reporte"') == 1
    assert b'"pages.sales_reports"' not in response.data
    nav = response.data.split(b'aria-label="Secciones del reporte"')[1].split(b'</nav>')[0]
    assert nav.count(b'<a ') == 5 and b'Tendencias' not in nav and b'tab=trends' not in nav
    assert b'/api/reports/sales/export' not in response.data  # la referencia nunca apunta a la API real
    assert response.data.count(b'data-rs-export=') == 2 and b'id="rs-export-csv"' in response.data and b'id="rs-export-pdf"' in response.data
    for method in ('post', 'put', 'patch', 'delete'):
        assert getattr(client, method)(route + '&ref=1').status_code == 405
    # Sin depuración el fixture no se sirve; la vista real necesita base de datos (cubierta por la suite e2e).
    app.debug = False
    assert b'"reference": true' not in client.get(route + '&ref=1', headers={'X-Preview-Role': 'anonymous'}).data
    app.debug = True
    for role, status in [('anonymous',401), ('vendedor',302), ('inventario',200), ('admin',200)]:
        assert client.get(route + '&ref=1', headers={'X-Preview-Role': role}).status_code == status
    bridge = client.get('/sales/reports?tab=' + tab + '&ref=1')
    assert bridge.status_code == 302
    assert bridge.location == '/reports/sales?tab=' + tab + '&ref=1'
legacy = client.get('/reports/sales?tab=trends&ref=1&start=2026-09-01')
assert legacy.status_code == 302 and legacy.location == '/reports/sales?tab=summary&ref=1&start=2026-09-01'
assert client.get('/reports/sales?tab=unknown&ref=1').status_code == 404
assert client.get('/sales/reports?ref=1', headers={'X-Preview-Role':'vendedor'}).status_code == 302
assert client.get('/sales/reports?ref=1', headers={'X-Preview-Role':'anonymous'}).status_code == 401
assert b'id="ix-nav-pred"' in client.get('/dashboard?ref=1').data
assert b'id="ix-nav-pred"' in client.get('/dashboard').data
assert client.get('/reports?ref=1').status_code == 200
print('PASS: five tabs, unknown-tab rejection, role permissions, legacy redirects, debug/ref isolation, rejected mutation methods, Inicio predictive navigation; no DB initialized or writes.')
