"""Disposable MariaDB harness for Sales Core. Hard-coded to ferreteria_conejo_e2e.

Requires customers/sales/sale_lines/quotes/quote_lines/orders/order_lines already migrated in
the E2E database (scripts/migrate_customers.py / migrate_sales.py / migrate_quotes.py /
migrate_orders.py --database ferreteria_conejo_e2e).
Seeds one operator, one vendedor, one category, E2E-CUST-* customers and E2E-PROD-*
products; self-cleaning: leftovers from previous runs are removed at startup and the
current run can be removed with POST /__sales_e2e__/cleanup. Never touches ferreteria_conejo.

Run with python -B scripts/serve_sales_e2e.py; stop after validation.
"""
import os
import secrets
import sys
from pathlib import Path

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import jsonify
from flask_login import login_user
from sqlalchemy import text
from app import create_app
from app.extensions import db
from app.models import Category, Customer, User
from app.services import product_service

PASSWORD = 'DisposableSalesOnly123!'
LONG_NAME = 'Distribuidora de Materiales Eléctricos, Ferretería y Construcción del Centro Occidente de Venezuela C.A.'
CLEANUP_SQL = [
    "DELETE FROM quote_lines WHERE product_id IN (SELECT id FROM products WHERE code LIKE 'E2E-PROD-%')",
    "DELETE FROM quotes WHERE seller_id IN (SELECT id FROM users WHERE username LIKE 'sales_e2e_%')",
    "DELETE FROM order_lines WHERE product_id IN (SELECT id FROM products WHERE code LIKE 'E2E-PROD-%')",
    "DELETE FROM orders WHERE seller_id IN (SELECT id FROM users WHERE username LIKE 'sales_e2e_%')",
    "DELETE FROM sale_lines WHERE product_id IN (SELECT id FROM products WHERE code LIKE 'E2E-PROD-%')",
    "DELETE FROM sales WHERE customer_id IN (SELECT id FROM customers WHERE name LIKE 'E2E-CUST-%' OR is_walk_in = 1) "
    "AND seller_id IN (SELECT id FROM users WHERE username LIKE 'sales_e2e_%')",
    "DELETE FROM stock_movements WHERE product_id IN (SELECT id FROM products WHERE code LIKE 'E2E-PROD-%')",
    "DELETE FROM products WHERE code LIKE 'E2E-PROD-%'",
    "DELETE FROM customers WHERE name LIKE 'E2E-CUST-%' AND is_walk_in = 0",
    "DELETE FROM categories WHERE name LIKE 'Ventas E2E %'",
    "DELETE FROM users WHERE username LIKE 'sales_e2e_%'",
]


class E2EConfig:
    SECRET_KEY = secrets.token_urlsafe(32)
    DEBUG = True
    APP_ENV = 'testing'
    MAIL_ENABLED = False
    GOOGLE_CLIENT_ID = None
    GOOGLE_CLIENT_SECRET = None
    GOOGLE_REDIRECT_URI = None
    SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://root:@127.0.0.1/ferreteria_conejo_e2e?charset=utf8mb4'
    SQLALCHEMY_TRACK_MODIFICATIONS = False


def cleanup():
    """Delete every synthetic E2E row (all runs). Only runs inside the guarded E2E app."""
    assert db.session.execute(text('SELECT DATABASE()')).scalar() == 'ferreteria_conejo_e2e'
    for statement in CLEANUP_SQL:
        db.session.execute(text(statement))
    db.session.commit()


def make_server():
    app = create_app(E2EConfig)
    with app.app_context():
        assert db.engine.url.database == 'ferreteria_conejo_e2e'
        engine = db.session.execute(text('SELECT VERSION()')).scalar()
        cleanup()
        run = secrets.token_hex(4)
        admin = User(name='Operador E2E Ventas', username='sales_e2e_' + run,
                     email='sales_' + run + '@example.invalid', role='admin')
        admin.set_password(PASSWORD)
        seller = User(name='Vendedor E2E', username='sales_e2e_v_' + run,
                      email='sales_v_' + run + '@example.invalid', role='vendedor')
        seller.set_password(PASSWORD)
        category = Category(name='Ventas E2E ' + run)
        customers = [Customer(name=f'E2E-CUST-{run}-{i:02} Constructora {i:02}', tax_id=f'J-{40000000 + int(run, 16) % 9000000 + i:08d}-{i % 10}',
                              phone=f'0412-{i:03d}-{int(run[:3], 16) % 10000:04d}') for i in range(12)]
        customers.append(Customer(name=f'E2E-CUST-{run}-LONG {LONG_NAME}'[:150],
                                  email=f'e2e_long_{run}@example.invalid', phone='0424-9999999-999999999'))
        db.session.add_all([admin, seller, category, *customers])
        db.session.commit()
        walk_in = Customer.query.filter(Customer.is_walk_in.is_(True)).first()
        context = {'database': 'ferreteria_conejo_e2e', 'engine': engine, 'run': run,
                   'username': admin.username, 'seller_username': seller.username, 'password': PASSWORD,
                   'walk_in_id': walk_in.id, 'customer_id': customers[0].id, 'long_customer_id': customers[-1].id,
                   'prefix': f'E2E-PROD-{run}'}
        with app.test_request_context():
            login_user(admin)
            products = []
            for index in range(6):
                product = product_service.create_product({
                    'code': f'E2E-PROD-{run}-{index:02}', 'name': f'Taladro E2E {index:02} percutor 1/2" 750W',
                    'category_id': category.id, 'current_stock': 30, 'minimum_stock': 5,
                    'purchase_price': '80.00', 'sale_price': '120.00',
                })
                products.append(product.id)
            long_product = product_service.create_product({
                'code': f'E2E-PROD-{run}-LONG', 'name': ('Cable THW calibre 12 AWG 600V cobre sólido rollo de 100 metros '
                                                          'color rojo certificado para instalaciones residenciales e industriales')[:150],
                'category_id': category.id, 'current_stock': 5, 'minimum_stock': 2,
                'purchase_price': '1.00', 'sale_price': '0.35',
            })
            context['product_ids'] = products
            context['long_product_id'] = long_product.id
            context['category_id'] = category.id

    @app.get('/__sales_e2e__/context')
    def test_context():
        # Only the dedicated local harness exposes this synthetic metadata.
        return jsonify(context)

    @app.post('/__sales_e2e__/cleanup')
    def test_cleanup():
        cleanup()
        return jsonify({'cleaned': True})

    return app


if __name__ == '__main__':
    port = int(os.environ.get('SALES_E2E_PORT', '5024'))
    make_server().run(host='127.0.0.1', port=port, debug=True, use_reloader=False, use_debugger=False)
