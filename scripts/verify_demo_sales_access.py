"""Guarded live DEMO 5001 regression. No sales/stock writes or volume operations.

Run via stdin inside the existing DEMO app container. Creates and closes test
sessions using the normal session service; never reads passwords or prints cookies.
"""
from flask import session
from flask_wtf.csrf import generate_csrf
import re
import requests
from sqlalchemy import text

from docker_support.guard import validate_demo


def history(engine):
    with engine.connect() as connection:
        assert connection.execute(text("SELECT DATABASE()")).scalar_one() == "conejo_demo_8b867d4b"
        count = connection.execute(text("SELECT COUNT(*) FROM sales")).scalar_one()
        checksums = connection.execute(text("CHECKSUM TABLE sales, sale_lines")).all()
        assert all(row[1] is not None for row in checksums)
        return count, checksums


def verify_role(app, user_id, role):
    from app.extensions import db
    from app.models import User
    from app.services import session_service

    base = "http://host.docker.internal:5001"
    with app.test_request_context(base_url=base):
        session_service.open_session(db.session.get(User, user_id))
        csrf = generate_csrf()
        cookie = app.session_interface.get_signing_serializer(app).dumps(dict(session))
    with requests.Session() as client:
        client.trust_env = False
        client.cookies.set(app.config["SESSION_COOKIE_NAME"], cookie)
        client.headers["X-CSRFToken"] = csrf
        try:
            landing = "/products" if role == "vendedor" else "/settings" if role == "soporte_ti" else "/dashboard"
            response = client.get(base + landing, timeout=30, allow_redirects=False)
            assert response.status_code == 200, (role, landing, response.status_code)
            navigation = "".join(re.findall(r"<aside\b.*?</aside>", response.text, re.DOTALL))
            assert navigation, (role, "missing sidebar")
            for path in ("/sales", "/sales/new", "/sales/list", "/sales/quotes", "/sales/orders"):
                assert f'href="{path}"' not in navigation, (role, path, "menu")
            for path in ("/sales", "/sales/new", "/sales/list", "/sales/quotes", "/sales/orders",
                         "/sales/VTA-000001", "/api/sales", "/api/sales/options", "/api/sales/dashboard",
                         "/api/sales/VTA-000001", "/api/quotes", "/api/orders"):
                denied = client.get(base + path, timeout=30, allow_redirects=False)
                assert denied.status_code == 403, (role, path, denied.status_code)
            for path in ("/api/sales", "/api/quotes", "/api/orders"):
                denied = client.post(base + path, json={}, timeout=30, allow_redirects=False)
                assert denied.status_code == 403, (role, path, denied.status_code)
            login = client.get(base + "/login", timeout=30, allow_redirects=False)
            assert login.status_code == 302 and login.headers["Location"].endswith(landing)
            print("PASS live navigation, RBAC, direct pages, APIs, login:", role, flush=True)
        finally:
            with app.test_request_context(base_url=base):
                session.update(app.session_interface.get_signing_serializer(app).loads(cookie))
                session_service.close_current()


def main():
    validate_demo()
    from docker_support.bootstrap import engine, identity
    from app import create_app
    from app.extensions import db
    from app.models import User

    target = engine()
    with target.connect() as connection:
        assert identity(connection).ready
    before = history(target)
    assert before[0] == 123, "Unexpected historical sales count"
    app = create_app()
    with app.app_context():
        users = {role: user_id for role, user_id in db.session.query(User.role, User.id).filter(User.is_active.is_(True)).all()}
    assert set(users) == {"admin", "inventario", "asistente", "vendedor", "soporte_ti"}
    for role, user_id in users.items():
        verify_role(app, user_id, role)
    with requests.Session() as anonymous:
        anonymous.trust_env = False
        base = "http://host.docker.internal:5001"
        for path in ("/sales", "/sales/new", "/sales/list", "/sales/quotes", "/sales/orders"):
            result = anonymous.get(base + path, timeout=30, allow_redirects=False)
            assert result.status_code == 302 and "/login" in result.headers["Location"]
        for path in ("/api/sales", "/api/quotes", "/api/orders"):
            assert anonymous.get(base + path, timeout=30).status_code == 401
            assert anonymous.post(base + path, json={}, timeout=30).status_code == 401
    assert history(target) == before, "Historical sales changed"
    target.dispose()
    print("PASS anonymous login/401; 123 historical sales and sale-line checksums unchanged")


if __name__ == "__main__":
    main()
