"""Local browser fixture with isolated SQLite and synthetic users only."""
from flask import abort, jsonify, redirect
from approval_test_support import IsolatedCase
from app.extensions import db
from app.models import User
from app.services import session_service

case = IsolatedCase()
case.setUp()
app = case.app


@app.get('/__reconciliation_fixture')
def fixture_marker():
    assert db.engine.dialect.name == 'sqlite'
    return jsonify(synthetic_sqlite=True)


@app.get('/__isolated_login/<actor>')
def fixture_login(actor):
    if actor not in ('admin', 'inventario', 'vendedor'):
        abort(404)
    session_service.open_session(User.query.filter_by(username=actor).one())
    return redirect('/reconciliation')


if __name__ == '__main__':
    try:
        app.run(host='127.0.0.1', port=5059, use_reloader=False, use_debugger=False, threaded=False)
    finally:
        db.session.remove()
        case.doCleanups()
