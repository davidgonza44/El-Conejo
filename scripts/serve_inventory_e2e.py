"""Disposable MariaDB browser harness. Only inserts synthetic data in the named E2E DB.

No migrations, table creation, deletion, operational credentials or dotenv loading.
Run with python -B scripts/serve_inventory_e2e.py; stop after browser validation.
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
from app.models import Category, User
from app.services import product_service, inventory_service


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


def make_server():
    app = create_app(E2EConfig)
    with app.app_context():
        assert db.engine.url.database == 'ferreteria_conejo_e2e'
        assert db.session.execute(text('SELECT DATABASE()')).scalar() == 'ferreteria_conejo_e2e'
        engine = db.session.execute(text('SELECT VERSION()')).scalar()
        run = secrets.token_hex(4)
        user = User(name='Operador E2E Movimientos', username='mv_e2e_' + run,
                    email='mv_' + run + '@example.invalid', role='admin')
        user.set_password('DisposableInventoryOnly123!')
        category = Category(name='Movimientos E2E ' + run)
        db.session.add_all([user, category])
        db.session.commit()
        context = {'database':'ferreteria_conejo_e2e', 'engine':engine, 'user_id':user.id,
                   'username':user.username, 'prefix':'MV-' + run}
        with app.test_request_context():
            login_user(user)
            products = []
            for index in range(12):
                product = product_service.create_product({
                    'code':f'MV-{run}-{index:02}', 'name':f'Martillo E2E {index:02}',
                    'category_id':category.id, 'current_stock':30,
                })
                products.append(product.id)
            context['product_id'] = products[0]
            inventory_service.register_exit({'product_id':products[1], 'quantity':2, 'reason':'Salida E2E inicial'})
            inventory_service.register_adjustment({'product_id':products[2], 'new_stock':25, 'reason':'Ajuste E2E inicial'})

    @app.get('/__inventory_e2e__/context')
    def test_context():
        # Only the dedicated local harness exposes this synthetic test metadata.
        return jsonify(context)

    return app


if __name__ == '__main__':
    port = int(os.environ.get('INVENTORY_E2E_PORT', '5023'))
    make_server().run(host='127.0.0.1', port=port, debug=True, use_reloader=False, use_debugger=False)
