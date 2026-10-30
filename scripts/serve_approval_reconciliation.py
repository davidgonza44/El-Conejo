"""Loopback UI fixture, in-memory SQLite only. Never start against real data."""
from flask import abort, redirect
from approval_test_support import IsolatedCase
from app.extensions import db
from app.services import session_service, purchase_service
from flask_login import login_user

case = IsolatedCase()
case.setUp()
app = case.app
app.debug = True
with app.test_request_context():
    login_user(case.users["inventario"])
    purchase_service.create_order({"supplier_id":case.supplier.id,
        "items":[{"product_id":case.product.id, "quantity":4, "unit_cost":"2.00"}]})


@app.get("/__isolated_login/<actor>")
def fixture_login(actor):
    if actor not in ("admin", "inventario", "vendedor"):
        abort(404)
    from app.models import User
    session_service.open_session(User.query.filter_by(username=actor).one())
    return redirect("/purchases")


if __name__ == "__main__":
    try:
        app.run(host="127.0.0.1", port=5049, use_reloader=False, use_debugger=False, threaded=False)
    finally:
        db.session.remove()
        case.doCleanups()
