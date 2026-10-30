"""Filtros y revisión HTTP en DEMO 5001; fixtures propios, stock cero y limpieza.

--ui conserva brevemente las cuatro fixtures para inspección visual en DEMO.
No lee archivos privados, migra tablas, reinicia datos ni accede al puerto 5000.
"""
import argparse
import time
import uuid
from pathlib import Path

from verify_demo_product_changes import authenticated, expect, snapshot
from docker_support.guard import validate_demo, validate_demo_url


def listing(admin, **filters):
    from urllib.parse import urlencode

    return expect(admin, 'GET', '/api/products?' + urlencode(filters), 200)


def verify(app, users, created, ui):
    prefix = 'DEMO-APF-' + uuid.uuid4().hex[:8]
    with authenticated(app, users['asistente']) as assistant, authenticated(app, users['admin']) as admin:
        categories = expect(assistant, 'GET', '/api/categories?status=active', 200)['items']
        assert len(categories) >= 2
        category = categories[0]['id']
        rows = {}
        for kind in ('new', 'change', 'approved', 'rejected'):
            row = expect(assistant, 'POST', '/api/products', 201, json={
                'code': prefix + '-' + kind, 'name': prefix + '-' + kind,
                'category_id': category, 'unit': 'unidad', 'current_stock': 0,
                'minimum_stock': 2, 'purchase_price': 2, 'sale_price': 3})
            created.append(row['id'])
            rows[kind] = row
            if kind in ('change', 'approved'):
                rows[kind] = expect(admin, 'POST', f"/api/products/{row['id']}/approve", 200)
            elif kind == 'rejected':
                rows[kind] = expect(admin, 'POST', f"/api/products/{row['id']}/reject", 200,
                                    json={'reason': 'Rechazo sintético de validación'})
        path = f"/api/products/{rows['change']['id']}"
        proposed = expect(assistant, 'PATCH', path, 200, json={
            'name': prefix + '-proposed', 'minimum_stock': 8})
        assert proposed['name'] == rows['change']['name'] and proposed['minimum_stock'] == 2
        assert proposed['pending_change']['changes']['minimum_stock'] == 8
        expected = {'pending': {rows['new']['id'], rows['change']['id']},
                    'approved': {rows['approved']['id']}, 'rejected': {rows['rejected']['id']}}
        for approval, ids in expected.items():
            result = listing(admin, approval_status=approval, search=prefix, page=1, per_page=8)
            assert {row['id'] for row in result['items']} == ids and result['count'] == len(ids)
        for status, kind in (('inactivo', 'new'), ('agotado', 'change')):
            filters = dict(approval_status='pending', search=prefix, category_id=category, status=status)
            result = listing(admin, **filters)
            assert {row['id'] for row in result['items']} == {rows[kind]['id']}
            assert listing(admin, **(filters | {'category_id': categories[1]['id']}))['count'] == 0
        for approval, kind, status in (('approved', 'approved', 'agotado'), ('rejected', 'rejected', 'inactivo')):
            result = listing(admin, approval_status=approval, search=rows[kind]['code'],
                             category_id=category, status=status)
            assert [row['id'] for row in result['items']] == [rows[kind]['id']]
        for page in (1, 2):
            result = listing(admin, approval_status='pending', search=prefix, page=page, per_page=1)
            assert result['pagination']['pages'] == 2 and len(result['items']) == 1
        empty = listing(admin, approval_status='pending', search=prefix + '-absent', page=1, per_page=8)
        assert empty['items'] == [] and empty['count'] == empty['pagination']['pages'] == 0
        for kind in ('new', 'change'):
            detail = expect(admin, 'GET', f"/api/products/{rows[kind]['id']}", 200)
            assert detail['proposal_author']
            if kind == 'new':
                assert detail['created_at'] and detail['proposed_by_user_id'] == users['asistente']
                assert detail['can_review_creation']
            else:
                assert detail['pending_change']['created_at'] and detail['can_review_change']
        page = expect(admin, 'GET', '/products?approval_status=pending', 200)
        assert 'Estado de aprobación' in page and 'Estado operativo' in page
        assert 'window.CAN_WRITE = false' in page
        dashboard = expect(admin, 'GET', '/dashboard', 200)
        assert '/products?approval_status=pending' in dashboard
        for kind in rows:
            expect(admin, 'PATCH', f"/api/products/{rows[kind]['id']}", 403, json={'name': 'Edición directa'})
        print('PASS DEMO HTTP: pendientes nuevos y modificaciones, aprobados, rechazados, filtros combinados, paginación, cero resultados, autor/fecha, enlace Dashboard y Admin sin editor.', flush=True)
        if ui:
            marker = Path('/tmp/product-approval-ui-' + uuid.uuid4().hex + '.done')
            print(f'UI READY prefix={prefix} category={category} marker={marker}', flush=True)
            deadline = time.monotonic() + 600
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(1)
            if not marker.exists():
                raise RuntimeError('Inspección UI agotada; se limpian las fixtures propias.')


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
            users = dict(db.session.query(User.role, User.id).filter(User.is_active.is_(True)).all())
            assert {'admin', 'asistente'} <= set(users)
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
        assert after == before, 'Cambió un dato original de DEMO.'
        target.dispose()
    print('PASS DEMO cleanup: checksums originales idénticos; fixtures propias retiradas y stock sin movimientos.', flush=True)


if __name__ == '__main__':
    main()
