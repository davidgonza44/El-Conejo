"""Reference layers only: reuse the existing synthetic-user, no-database harness."""
from copy import deepcopy
import re
from unittest.mock import patch

from test_settings_reference import app
from app.utils.inventory_reference import MOVEMENTS
from app.utils.navigation import home_path, nav_allowed, settings_tab_allowed, visible_links
from app.utils.permissions import ROLE_PERMISSIONS, role_has_permission
from flask_wtf.csrf import generate_csrf
from flask_login import current_user

# The shared no-DB harness predates persisted RBAC and shell URL resolution.
app.add_template_global(lambda permission: role_has_permission(current_user.role, permission), 'has_permission')
app.add_template_global(lambda key, reference=False: nav_allowed(current_user.role, key, reference is True), 'nav_allowed')
app.add_template_global(lambda children, reference=False: visible_links(current_user.role, children, reference is True), 'visible_nav')
app.add_template_global(lambda key, reference=False: settings_tab_allowed(current_user.role, key, reference is True), 'settings_tab_allowed')
app.add_template_global(lambda: home_path(current_user.role) if getattr(current_user, 'role', None) else '/login', 'home_path')
app.add_template_global(lambda: None, 'get_company_logo_url')
app.add_template_global(generate_csrf, 'csrf_token')
app.add_url_rule('/purchases', endpoint='purchases.page', view_func=lambda: ('', 404))
app.add_url_rule('/purchases/receptions', endpoint='purchases.receptions', view_func=lambda: ('', 404))
app.add_url_rule('/reconciliation', endpoint='reconciliation.page', view_func=lambda: ('', 404))


@patch('app.services.rbac_service.permissions_for', side_effect=lambda role: ROLE_PERMISSIONS.get(role, set()))
def verify_reference_layers(_permissions):
    original = deepcopy(MOVEMENTS)
    client = app.test_client()
    response = client.get('/inventory?ref=1')
    assert response.status_code == 200
    markup = response.get_data(as_text=True)
    for marker in ('mv-drawer', 'mv-export', 'mv-filters-template', 'mv-activities-template', 'mv-detail-ENT-0098'):
        assert marker in markup, marker
    def detail(reference, html=markup):
        return re.search(r'<template id="mv-detail-' + reference + r'">(.*?)</template>', html, re.S).group(1)
    pending = detail('AJU-0025')
    completed = detail('ENT-0098')
    assert 'data-mv-edit' in pending and 'data-mv-correction' not in pending
    assert 'aún no ha sido aplicado' in pending
    assert 'Ajuste de inventario por conteo físico' in pending
    assert 'data-mv-edit' not in completed and 'data-mv-correction' in completed
    assert 'ya fue aplicado' in completed
    table = re.search(r'<tbody>(.*?)</tbody>', markup, re.S).group(1)
    assert table.count('aria-label="Ver detalle"') == 6
    assert 'Editar movimiento' not in table
    # Render canceled semantics using a private copy, without adding a fixture.
    canceled = deepcopy(MOVEMENTS)
    canceled['history']['rows'][0]['status'] = ('Cancelado', 'gray')
    with app.test_request_context('/inventory?ref=1'):
        html = app.jinja_env.get_template('inventory/_movement_layers.html').render(fixture=canceled)
    assert 'data-mv-edit' not in detail('ENT-0098', html)
    assert 'data-mv-correction' not in detail('ENT-0098', html)
    for method in ('post', 'put', 'patch', 'delete'):
        assert getattr(client, method)('/inventory?ref=1').status_code == 405
    assert client.get('/inventory?ref=1', headers={'X-Preview-Role': 'anonymous'}).status_code == 401
    assert client.get('/inventory?ref=1', headers={'X-Preview-Role': 'vendedor'}).status_code == 403
    assert client.get('/inventory?ref=1', headers={'X-Preview-Role': 'inventario'}).status_code == 200
    live = client.get('/inventory').get_data(as_text=True)
    for html in (markup, live):
        module = html[html.index('Registro de entradas, salidas y ajustes de inventario'):]
        assert 'Transferencia' not in module and 'Sucursal' not in module
        assert 'Movimientos registrados' in module and 'Historial total' in module
        assert module.count('<th scope=') == 10
        assert 'mv-branch' not in module and 'mv-filter-branch' not in module
        assert 'mv-activity-branch' not in module
    assert 'Sucursal' not in live and 'mv-location' not in live
    assert 'ix_movements_live.js' in live and 'id="mv-fixture"' not in live
    assert 'ENT-0098' not in live and 'admin@ferreteriapro.com' not in live
    def activity_template(html):
        return re.search(r'<template id="mv-activities-template">(.*?)</template>', html, re.S).group(1)
    reference_activities = activity_template(markup)
    live_activities = activity_template(live)
    for html in (reference_activities, live_activities):
        for marker in ('mv-activities', 'mv-activity-search', 'mv-activity-kind',
                       'mv-activity-state', 'mv-activity-date',
                       'data-activity-count', 'data-activity-order', 'mv-activity-list'):
            assert marker in html, marker
        assert 'mv-footer' not in html
    assert reference_activities.count('data-activity-ref=') == 8
    assert 'data-activity-ref=' not in live_activities
    for kind, tone in [('entrada', 'green'), ('salida', 'red'), ('ajuste', 'purple')]:
        row = re.search(r'<template id="mv-activity-row-' + kind + r'">(.*?)</template>', live, re.S).group(1)
        assert 'mv-activity-icon mv-' + tone in row
        assert 'mv-activity-copy' in row and 'mv-activity-side' in row
        assert '<svg' in row and 'mv-badge' not in row
    app.debug = False
    assert 'id="mv-fixture"' not in client.get('/inventory?ref=1').get_data(as_text=True)
    app.debug = True
    assert MOVEMENTS == original
    assert len(MOVEMENTS['history']['rows']) == 6
    first = MOVEMENTS['history']['rows'][0]
    assert (first['ref'], first['qty'], first['before'], first['after']) == ('ENT-0098', '10', '138', '148')
    assert 'sqlalchemy' not in app.extensions
    print('PASS: debug/ref isolation, authentication, role checks, rejected mutation methods, fixture integrity; no database initialized')


if __name__ == '__main__':
    verify_reference_layers()
