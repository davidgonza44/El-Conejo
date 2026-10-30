"""Disposable quick-action harness. Existing E2E schema only; never runs migrations."""
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import abort, jsonify, redirect, request
from sqlalchemy import inspect, text
from app import create_app
from app.extensions import db
from app.models import Category, Product, User, Role, Permission
from app.models.purchase import Supplier, PurchaseOrder
from app.models.stock_movement import StockMovement
from app.services import dashboard_service, notification_providers, session_service
from purchase_e2e_support import DATABASE, guard_engine, cleanup
from migrate_purchase_orders import engine_for


def make_server():
    engine = engine_for(DATABASE)
    with engine.connect() as connection:
        assert connection.execute(text('SELECT DATABASE()')).scalar() == DATABASE
        assert all(inspect(connection).has_table(name) for name in
                   ('purchase_orders', 'purchase_order_lines', 'suppliers', 'roles', 'permissions', 'user_sessions'))
    uri = engine.url.render_as_string(hide_password=False)
    engine.dispose()

    class Config:
        SECRET_KEY = secrets.token_urlsafe(32)
        DEBUG = True
        TESTING = True
        APP_ENV = 'testing'
        MAIL_ENABLED = False
        SQLALCHEMY_DATABASE_URI = uri
        SQLALCHEMY_TRACK_MODIFICATIONS = False

    app = create_app(Config)
    writes = []
    with app.app_context():
        guard_engine(db.engine, writes)
        assert db.session.execute(text('SELECT DATABASE()')).scalar() == DATABASE
        run = secrets.token_hex(4)
        permissions = Permission.query.filter(Permission.code.in_(('purchases:read', 'purchases:write'))).all()
        assert len(permissions) == 2
        role = Role(slug='dpo_' + run, name='Dashboard purchase E2E ' + run, permissions=permissions)
        users = [User(name=label, username=f'dpo_{index}_{run}', email=f'dpo_{index}_{run}@example.invalid',
                      role=slug, password_changed_at=datetime.utcnow()) for index, (label, slug) in enumerate(
                          [('Operador de pruebas', 'admin'), ('Comprador de pruebas', role.slug), ('Sin permiso de compras', 'inventario')])]
        category = Category(name='Dashboard purchase E2E ' + run)
        db.session.add_all([role, category, *users])
        db.session.flush()
        product = Product(code='DPO-' + run, name='Producto de prueba de compra', category_id=category.id,
                          current_stock=20, minimum_stock=0, purchase_price=25, sale_price=35, is_active=True)
        supplier = Supplier(name='Proveedor de prueba de compra', tax_id='DPO-' + run, is_active=True)
        db.session.add_all([product, supplier])
        db.session.commit()
        context = dict(run=run, database=DATABASE, admin_id=users[0].id, reader_id=users[1].id,
                       denied_id=users[2].id, role_id=role.id, category_id=category.id,
                       product_ids=[product.id], supplier_ids=[supplier.id])

    def deny_external(*args, **kwargs):
        raise notification_providers.ProviderError('External delivery disabled in disposable E2E.')
    notification_providers.send = deny_external

    @app.before_request
    def only_loopback():
        if request.remote_addr not in ('127.0.0.1', '::1'):
            abort(403)
        if request.path.startswith('/media/'):
            abort(404)

    @app.get('/__dashboard_purchase_e2e__/login/<kind>')
    def login(kind):
        if kind not in ('admin', 'reader', 'denied'):
            abort(404)
        session_service.open_session(db.session.get(User, context[kind + '_id']))
        return redirect('/purchases?new=1' if kind == 'reader' else '/dashboard')

    @app.get('/__dashboard_purchase_e2e__/state')
    def state():
        product = db.session.get(Product, context['product_ids'][0])
        writer = db.session.get(User, context['reader_id'])
        denied = db.session.get(User, context['denied_id'])
        return jsonify(**context, writes=len(writes), stock=product.current_stock,
                       orders=PurchaseOrder.query.filter(PurchaseOrder.created_by_user_id.in_(
                           [context['admin_id'], context['reader_id'], context['denied_id']])).count(),
                       movements=StockMovement.query.filter_by(product_id=product.id).count(),
                       dynamic_action=dashboard_service.quick_actions(writer.role)[2],
                       denied_action=dashboard_service.quick_actions(denied.role)[2])

    @app.post('/__dashboard_purchase_e2e__/reader-only')
    def reader_only():
        role = db.session.get(Role, context['role_id'])
        role.permissions = [permission for permission in role.permissions if permission.code != 'purchases:write']
        db.session.commit()
        return jsonify(ok=True)

    @app.post('/__dashboard_purchase_e2e__/cleanup')
    def clean():
        cleanup(app, context)
        return jsonify(cleaned=True)

    return app


if __name__ == '__main__':
    make_server().run(host='127.0.0.1', port=51439, debug=True, use_reloader=False, use_debugger=False)
