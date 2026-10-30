"""Loopback-only browser harness; synthetic E2E users/data, never the valuable DB."""
from flask import jsonify, redirect, request, abort
from purchase_e2e_support import make_app, client_for, cleanup, DATABASE
from app.extensions import db
from app.models import User
from app.services import session_service
from sqlalchemy import text


def make_server():
    app, context, writes = make_app(seed_orders=True, reuse=True)

    @app.before_request
    def only_loopback():
        if request.remote_addr not in ("127.0.0.1", "::1"):
            abort(403)

    @app.get('/__purchases_e2e__/login')
    def login():
        assert db.session.execute(text('SELECT DATABASE()')).scalar() == DATABASE
        session_service.open_session(db.session.get(User, context['admin_id']))
        return redirect('/purchases')

    @app.get('/__purchases_e2e__/context')
    def synthetic_context():
        return jsonify(context)

    @app.get('/__purchases_e2e__/write-count')
    def write_count():
        return jsonify(count=len(writes), database=DATABASE)

    @app.post('/__purchases_e2e__/cleanup')
    def clean():
        cleanup(app, context)
        return jsonify(cleaned=True)

    return app


if __name__ == '__main__':
    make_server().run(host='127.0.0.1', port=5032, debug=False, use_reloader=False, use_debugger=False)
