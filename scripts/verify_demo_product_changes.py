"""HTTP real exclusivo de DEMO 5001; fixtures propios y checksums antes/después.

No usa contraseñas ni archivos privados. Las sesiones de prueba se abren mediante
el servicio existente, conservan CSRF y se cierran al finalizar. Solo limpia IDs
que la API devolvió al crear productos sintéticos con stock inicial cero.
"""
import argparse
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docker_support.guard import validate_demo, validate_demo_url
from sqlalchemy import text
import requests
from flask import session
from flask_wtf.csrf import generate_csrf

BASE = 'http://host.docker.internal:5001'


@contextmanager
def authenticated(app, user_id):
    from app.extensions import db
    from app.models import User
    from app.services import session_service
    with app.test_request_context(base_url=BASE):
        user = db.session.get(User, user_id)
        previous_login = user.last_login_at
        previous_updated = user.updated_at
        session_service.open_session(user)
        test_login = user.last_login_at
        test_updated = user.updated_at
        record_id = session_service.current_record().id
        csrf = generate_csrf()
        cookie = app.session_interface.get_signing_serializer(app).dumps(dict(session))
    with requests.Session() as client:
        client.trust_env = False
        client.cookies.set(app.config['SESSION_COOKIE_NAME'], cookie)
        client.headers['X-CSRFToken'] = csrf
        try:
            yield client
        finally:
            with app.test_request_context(base_url=BASE):
                session.update(app.session_interface.get_signing_serializer(app).loads(cookie))
                session_service.close_current()
                # Revierte solo la marca producida por esta sesión. Un login ajeno
                # concurrente no se sobrescribe. Las sesiones de usuario siguen intactas.
                User.query.filter_by(id=user_id, last_login_at=test_login, updated_at=test_updated).update(
                    {User.last_login_at: previous_login, User.updated_at: previous_updated}, synchronize_session=False)
                from app.models import UserSession
                UserSession.query.filter_by(id=record_id, user_id=user_id).delete(synchronize_session=False)
                db.session.commit()


def snapshot(engine):
    from docker_support.demo import require_ready
    with engine.connect() as connection:
        require_ready(connection)
        rows = connection.execute(text('CHECKSUM TABLE products, categories, stock_movements, '
                                       'product_state_histories, product_changes, users, roles, role_permissions')).all()
        if not all(row[1] is not None for row in rows):
            raise RuntimeError('No se pueden verificar los datos originales de DEMO.')
        return rows


def expect(client, method, path, status, **kwargs):
    response = client.request(method, BASE + path, timeout=30, allow_redirects=False, **kwargs)
    if response.status_code != status:
        raise AssertionError(f'{method} {path}: esperaba {status}; recibió {response.status_code}')
    if response.headers.get('Content-Type', '').startswith('application/json'):
        return response.json()
    return response.text


def new_product(assistant, admin, category, code, created):
    result = expect(assistant, 'POST', '/api/products', 201, json={
        'code': code, 'name': code, 'category_id': category,
        'unit': 'unidad', 'current_stock': 0, 'minimum_stock': 2,
        'purchase_price': '2.00', 'sale_price': '3.00', 'brand': 'Inicial', 'description': 'Prueba sintética'})
    created.append(result['id'])
    assert result['approval_status'] == 'pending' and not result['is_active']
    return expect(admin, 'POST', f"/api/products/{result['id']}/approve", 200)


def decision(client, product_id, proposal_id, approve=True, status=200):
    body = {'proposal_id': proposal_id}
    if not approve:
        body['reason'] = 'Rechazo de prueba DEMO'
    return expect(client, 'POST', f"/api/products/{product_id}/{'approve' if approve else 'reject'}", status, json=body)


def verify(app, users, created, ui):
    prefix = 'DEMO-CHG-' + uuid.uuid4().hex[:10]
    with authenticated(app, users['asistente']) as assistant, authenticated(app, users['admin']) as admin, \
            authenticated(app, users['inventario']) as inventory:
        categories = expect(assistant, 'GET', '/api/categories?status=active', 200)['items']
        assert len(categories) >= 2
        original = new_product(assistant, admin, categories[0]['id'], prefix, created)
        path = f"/api/products/{original['id']}"
        for client in (admin, inventory):
            for method in ('PUT', 'PATCH', 'DELETE'):
                expect(client, method, path, 403, json={'name': 'Intento directo'})
            expect(client, 'DELETE', path + '/image', 403)
            expect(client, 'POST', path + '/image', 403)
        page = expect(admin, 'GET', '/products', 200)
        assert 'window.CAN_WRITE = false' in page and 'id="pl-action" class="pl-primary" hidden' in page
        changes = {'name': prefix + ' aprobado', 'category_id': categories[1]['id'], 'unit': 'caja',
                   'description': 'Cambio aprobado de prueba', 'brand': 'Propuesta', 'purchase_price': '5.25',
                   'sale_price': '9.75', 'minimum_stock': 7, 'is_active': False}
        proposed = expect(assistant, 'PUT', path, 200, json=changes)
        proposal = proposed['pending_change']
        assert proposal['changes'] == changes and proposal['status'] == 'pending'
        # Incluye timestamps: proponer no escribe el registro de Product.
        for field, value in original.items():
            if field not in ('pending_change', 'last_change', 'can_review_change'):
                assert proposed[field] == value, field
        decision(assistant, original['id'], proposal['id'], status=403)
        decision(inventory, original['id'], proposal['id'], status=403)
        expect(assistant, 'PATCH', path, 409, json={'name': 'Sobrescritura'})
        approved = decision(admin, original['id'], proposal['id'])
        for field, value in changes.items():
            assert approved[field] == (float(value) if field in ('purchase_price', 'sale_price') else value), field
        assert approved['current_stock'] == 0 and approved['code'] == original['code']
        assert approved['pending_change'] is None and approved['last_change']['status'] == 'approved'
        decision(admin, original['id'], proposal['id'], status=409)
        rejected_proposal = expect(assistant, 'PATCH', path, 200, json={
            'name': 'No aplicar', 'unit': 'metro', 'purchase_price': '88.00', 'minimum_stock': 99, 'is_active': True})['pending_change']
        expect(admin, 'POST', path + '/reject', 400, json={'proposal_id': rejected_proposal['id']})
        rejected = decision(admin, original['id'], rejected_proposal['id'], False)
        for field, value in approved.items():
            if field not in ('pending_change', 'last_change', 'can_review_change'):
                assert rejected[field] == value, field
        assert rejected['last_change']['status'] == 'rejected'
        # Dos peticiones concurrentes no pueden decidir dos veces la misma propuesta.
        race = expect(assistant, 'PATCH', path, 200, json={'name': prefix + ' carrera'})['pending_change']
        with authenticated(app, users['admin']) as other_admin:
            def submit(client):
                return client.post(BASE + path + '/approve', json={'proposal_id': race['id']}, timeout=30).status_code
            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = list(pool.map(submit, (admin, other_admin)))
            assert sorted(statuses) == [200, 409], statuses
        # Lectura bloqueante de pendientes: también se serializan dos proponentes.
        with authenticated(app, users['asistente']) as other_assistant:
            def propose_once(pair):
                client, suffix = pair
                return client.patch(BASE + path, json={'name': prefix + suffix}, timeout=30).status_code
            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = list(pool.map(propose_once, ((assistant, ' A'), (other_assistant, ' B'))))
            assert sorted(statuses) == [200, 409], statuses
        concurrent_proposal = expect(admin, 'GET', path, 200)['pending_change']
        decision(admin, original['id'], concurrent_proposal['id'], False)
        clear_brand = expect(assistant, 'PATCH', path, 200, json={'brand': ''})['pending_change']
        assert clear_brand['changes'] == {'brand': None}
        assert expect(admin, 'GET', path, 200)['brand'] == 'Propuesta'
        assert decision(admin, original['id'], clear_brand['id'])['brand'] is None
        print('PASS DEMO HTTP: Admin/Inventario PUT/PATCH/DELETE e imagen 403; Asistente propone; Admin aprueba/rechaza; datos vigentes y stock conservados hasta aprobación.', flush=True)
        print('PASS DEMO MariaDB: dos decisiones y dos propuestas concurrentes devuelven 200 + 409; borrado explícito de marca solo al aprobar.', flush=True)
        if ui:
            ui_product = new_product(assistant, admin, categories[0]['id'], prefix + '-UI', created)
            expect(assistant, 'PUT', f"/api/products/{ui_product['id']}", 200, json={'name': prefix + '-UI propuesto', 'minimum_stock': 8})
            marker = Path('/tmp/product-changes-ui-' + uuid.uuid4().hex + '.done')
            print(f'UI READY code={ui_product["code"]} marker={marker}', flush=True)
            deadline = time.monotonic() + 240
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(1)
            if not marker.exists():
                raise RuntimeError('Se agotó el tiempo de inspección UI; se limpian los fixtures propios.')
            print('PASS DEMO UI: inspección completada; comienza limpieza de fixtures.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ui', action='store_true')
    args = parser.parse_args()
    validate_demo()
    from app import create_app
    from app.extensions import db
    from app.models import Product, ProductChange, ProductStateHistory, StockMovement, User
    from docker_support.bootstrap import engine
    target = engine()
    validate_demo_url(target.url)
    before = snapshot(target)
    app = create_app()
    created = []
    try:
        with app.app_context():
            validate_demo_url(db.engine.url)
            users = {role: user_id for role, user_id in db.session.query(User.role, User.id).filter(User.is_active.is_(True)).all()}
            assert {'admin', 'asistente', 'inventario'} <= set(users)
        verify(app, users, created, args.ui)
    finally:
        with app.app_context():
            db.session.rollback()
            if created:
                assert StockMovement.query.filter(StockMovement.product_id.in_(created)).count() == 0
                ProductChange.query.filter(ProductChange.product_id.in_(created)).delete(synchronize_session=False)
                ProductStateHistory.query.filter(ProductStateHistory.product_id.in_(created)).delete(synchronize_session=False)
                Product.query.filter(Product.id.in_(created)).delete(synchronize_session=False)
                db.session.commit()
        after = snapshot(target)
        changed = [row[0].rsplit('.', 1)[-1] for row, previous in zip(after, before) if row != previous]
        assert not changed, 'Cambió un dato original de DEMO en: ' + ', '.join(changed)
        target.dispose()
    print('PASS DEMO cleanup: checksums idénticos de productos, categorías, movimientos, estados, propuestas previas, usuarios y RBAC.', flush=True)


if __name__ == '__main__':
    main()
