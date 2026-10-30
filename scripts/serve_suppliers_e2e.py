"""Loopback supplier UI harness. Every SQL write asserts the disposable database."""
from flask import abort, jsonify, redirect, request
from flask_login import login_user
from sqlalchemy import text
from purchase_e2e_support import make_app, cleanup, DATABASE
from migrate_suppliers_full import migrate, engine_for
from app.extensions import db
from app.models import User
from app.services import session_service, supplier_service, purchase_service


def make_server():
    engine = engine_for(DATABASE)
    migrate(engine, DATABASE)
    engine.dispose()
    app, context, writes = make_app(seed_orders=True)
    with app.app_context(), app.test_request_context():
        assert db.session.execute(text('SELECT DATABASE()')).scalar() == DATABASE
        login_user(db.session.get(User, context['admin_id']))
        for i in range(12):
            data = dict(name=f'E2E {("Materiales del Centro", "Ferretería Industrial", "Suministros del Norte", "Construcciones del Sur")[i % 4]} {context["run"]}-{i+1}',
                        tax_id=f'SUPUI-{context["run"]}-{i}', contact_name='Contacto de pruebas', contact_title='Gerente de compras',
                        phone='0412-555.0100', email='compras@example.invalid', category=('Materiales','Herramientas','Construcción')[i % 3],
                        city=('Valencia','Caracas','Maracay')[i % 3], address='Dirección sintética E2E', state_province='Carabobo',
                        notes='Datos sintéticos exclusivos de ferreteria_conejo_e2e.', is_active=i != 3)
            if i < 4:
                supplier = supplier_service.save(data, context['supplier_ids'][i])
            else:
                supplier = supplier_service.save(data)
                context['supplier_ids'].append(supplier.id)
                order = purchase_service.create_order({'supplier_id':supplier.id,'items':[{'product_id':context['product_ids'][0],'quantity':i+1,'unit_cost':'80.00'}]})
                context['order_ids'].append(order.id)
            if i % 3 == 0:
                supplier_service.change(supplier.id, 'preferred')

    @app.before_request
    def only_loopback():
        if request.remote_addr not in ('127.0.0.1','::1'):
            abort(403)

    @app.get('/__suppliers_e2e__/login')
    def login():
        assert db.session.execute(text('SELECT DATABASE()')).scalar() == DATABASE
        session_service.open_session(db.session.get(User, context['admin_id']))
        return redirect('/suppliers?search='+context['run'])

    @app.get('/__suppliers_e2e__/context')
    def info():
        return jsonify(context)

    @app.get('/__suppliers_e2e__/write-count')
    def write_count():
        return jsonify(count=len(writes), database=DATABASE)

    @app.post('/__suppliers_e2e__/cleanup')
    def clean():
        cleanup(app, context)
        return jsonify(cleaned=True)

    return app


if __name__ == '__main__':
    make_server().run(host='127.0.0.1',port=5033,debug=False,use_reloader=False,use_debugger=False)
