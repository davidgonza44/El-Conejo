"""One marked synthetic SR/quote/OC evidence. Guarded DEMO 5001 only.

create: Inventory uses predictive API. UI performs start/quote/prepare.
approve: Administrator approves only this marked SR's linked OC.
status: read-only stock/movement/reception comparison.
"""
import json
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docker_support.guard import validate_demo, validate_demo_url
from verify_demo_category_approval import authenticated, expect

NOTE = 'AUDITORIA INTEGRAL SR 2026-10-08 - evidencia conservada'


def main():
    validate_demo()
    from app import create_app
    from app.extensions import db
    from app.models import Product, User, StockMovement
    from app.models.purchase import PurchaseOrder, PurchaseReception
    from app.models.replenishment_request import ReplenishmentRequest
    from app.models.supplier_quote import SupplierQuote
    from docker_support.demo import require_ready
    app = create_app()
    with app.app_context():
        validate_demo_url(db.engine.url)
        with db.engine.connect() as connection:
            require_ready(connection)
        row = ReplenishmentRequest.query.filter_by(notes=NOTE).one_or_none()
        role = 'inventario' if sys.argv[1] == 'create' else 'admin'
        user_id = User.query.filter_by(role=role, is_active=True).first().id
        product_id = Product.query.filter_by(code='DEMO-003', is_active=True, approval_status='approved').one().id
        order_id = row.purchase_order_id if row else None
        rid = row.id if row else None
    if sys.argv[1] == 'create':
        assert rid is None, 'Evidence already exists; no duplicate allowed'
        with authenticated(app, user_id) as client:
            row = expect(client, 'POST', '/api/predictions/replenishment/orders', 201, json={
                'confirm': True, 'notes': NOTE, 'idempotency_key': 'sr-evidence-oct08-2026',
                'items': [{'product_id': product_id, 'quantity': 2}]})
            print(json.dumps({'SR': row['request_number'], 'id': row['id'], 'creator': row['creator'],
                              'origin': row['origin'], 'stock': row['items'][0]['stock_on_hand']}))
    elif sys.argv[1] == 'approve':
        assert order_id and rid != 3, 'Only the marked evidence order is authorized'
        with authenticated(app, user_id) as client:
            order = expect(client, 'POST', f'/api/purchases/{order_id}/approve', 200, json={})
            assert order['status'] == 'approved'
            print(json.dumps({'OC': order['order_number'], 'id': order_id, 'admin': order['approved_by_user_id'],
                              'status': order['status'], 'approved_at': order['approved_at']}))
    elif sys.argv[1] != 'status':
        raise SystemExit('Use create|approve|status')
    with app.app_context():
        sr = ReplenishmentRequest.query.filter_by(notes=NOTE).one_or_none()
        print(json.dumps({'products': Product.query.count(), 'stock_sum': int(db.session.execute(text(
            'SELECT SUM(current_stock) FROM products')).scalar_one()),
            'DEMO-003_stock': db.session.get(Product, product_id).current_stock,
            'movements': StockMovement.query.count(), 'orders': PurchaseOrder.query.count(),
            'receptions': PurchaseReception.query.count(), 'requests': ReplenishmentRequest.query.count(),
            'quotes': SupplierQuote.query.count(), 'SR': sr.to_dict() if sr else None}, ensure_ascii=False))


if __name__ == '__main__':
    main()
