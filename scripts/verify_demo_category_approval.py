"""Verificación exclusiva de DEMO 5001: HTTP real y decisiones concurrentes en MariaDB.

No lee contraseñas ni archivos privados. Crea categorías y un segundo administrador
temporales sin credenciales, y elimina únicamente sus IDs en finally. Compara
categorías originales y checksums de productos/movimientos antes y después.
"""
import uuid
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from unittest.mock import patch

import requests
from flask import session
from flask_login import login_user
from flask_wtf.csrf import generate_csrf
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docker_support.guard import validate_demo, validate_demo_url

BASE = "http://host.docker.internal:5001"


def snapshot(engine):
    with engine.connect() as connection:
        from docker_support.demo import require_ready
        require_ready(connection)
        categories = connection.execute(text(
            "SELECT id,name,description,is_active,color,icon,created_at FROM categories ORDER BY id")).all()
        checksums = connection.execute(text("CHECKSUM TABLE products, stock_movements, product_state_histories")).all()
        if not all(row[1] is not None for row in checksums):
            raise RuntimeError("No se pudieron verificar los checksums de los datos de DEMO.")
        return categories, checksums


@contextmanager
def authenticated(app, user_id):
    from app.extensions import db
    from app.models import User
    from app.services import session_service
    with app.test_request_context(base_url=BASE):
        session_service.open_session(db.session.get(User, user_id))
        csrf = generate_csrf()
        cookie = app.session_interface.get_signing_serializer(app).dumps(dict(session))
    with requests.Session() as client:
        client.trust_env = False
        client.cookies.set(app.config["SESSION_COOKIE_NAME"], cookie)
        client.headers["X-CSRFToken"] = csrf
        try:
            yield client
        finally:
            with app.test_request_context(base_url=BASE):
                session.update(app.session_interface.get_signing_serializer(app).loads(cookie))
                session_service.close_current()


def expect(client, method, path, status, **kwargs):
    response = client.request(method, BASE + path, timeout=30, allow_redirects=False, **kwargs)
    assert response.status_code == status, (method, path, response.status_code, response.text[:180])
    return response.json() if response.headers.get("Content-Type", "").startswith("application/json") else response.text


def propose(client, name, created):
    row = expect(client, "POST", "/api/categories", 201,
                 json={"name": name, "is_active": True, "approval_status": "approved"})
    created.append(row["id"])
    assert row["approval_status"] == "pending" and row["is_active"] is False
    return row


def concurrent_decisions(app, category_id, administrators, decisions):
    from app.extensions import db
    from app.models import Category, User
    from app.services import category_service
    from app.services.exceptions import ConflictError
    original = category_service.get_category_or_404
    barrier = Barrier(2)
    def stale_read(key):
        category = original(key)
        barrier.wait(timeout=15)
        return category
    def decide(pair):
        user_id, approve = pair
        with app.test_request_context():
            login_user(db.session.get(User, user_id))
            try:
                category_service.decide_category(category_id, approve=approve, reason="Validación concurrente DEMO")
                return 200, user_id, approve
            except ConflictError:
                return 409, user_id, approve
    with patch.object(category_service, "get_category_or_404", side_effect=stale_read):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(decide, zip(administrators, decisions)))
    assert sorted(row[0] for row in results) == [200, 409], results
    winner = next(row for row in results if row[0] == 200)
    with app.app_context():
        category = db.session.get(Category, category_id)
        assert category.approved_by_user_id == winner[1]
        assert category.is_active == winner[2]
        assert category.approval_status == ("approved" if winner[2] else "rejected")


def verify(app, users, created, second_admin_id):
    prefix = "Prueba categorías " + uuid.uuid4().hex[:10]
    with authenticated(app, users["asistente"]) as assistant, authenticated(app, users["admin"]) as admin, \
            authenticated(app, users["inventario"]) as inventory:
        # Exactamente las diez originales; contratos API con 10 y con 6.
        first = expect(assistant, "GET", "/api/categories?page=1&per_page=10", 200)
        assert first["count"] == len(first["items"]) == 10 and first["pagination"]["pages"] == 1
        assert all(row["approval_status"] == "approved" for row in first["items"])
        six = expect(assistant, "GET", "/api/categories?page=1&page_size=6", 200)
        four = expect(assistant, "GET", "/api/categories?page=2&per_page=6", 200)
        assert len(six["items"]) == 6 and len(four["items"]) == 4
        assert six["pagination"]["has_next"] and not four["pagination"]["has_next"]
        assert {row["id"] for row in six["items"]}.isdisjoint(row["id"] for row in four["items"])
        for client in (inventory, admin):
            expect(client, "POST", "/api/categories", 403, json={"name": prefix})
        category = propose(assistant, prefix, created)
        path = f"/api/categories/{category['id']}"
        expect(assistant, "POST", path + "/approve", 403)
        expect(inventory, "POST", path + "/approve", 403)
        expect(assistant, "POST", "/api/categories", 409, json={"name": "  " + prefix.upper().replace(" ", "   ") + "  "})
        expect(assistant, "PUT", path, 400, json={"is_active": True})
        expect(admin, "PUT", path, 409, json={"is_active": True})
        usable = expect(assistant, "GET", "/api/categories?status=active", 200)
        assert category["id"] not in {row["id"] for row in usable["items"]}
        expect(assistant, "POST", "/api/products", 400, json={"code": prefix, "name": prefix,
               "category_id": category["id"], "unit": "unidad", "purchase_price": 1, "sale_price": 2})
        pending = expect(assistant, "GET", "/api/categories?status=pending&search=" + prefix, 200)
        assert pending["count"] == 1
        csv = expect(assistant, "GET", "/api/categories/export?format=csv&status=pending&search=" + prefix, 200)
        assert prefix in csv and "Pendientes" in csv
        approved = expect(admin, "POST", path + "/approve", 200)
        assert approved["approval_status"] == "approved" and approved["is_active"] is True
        expect(admin, "POST", path + "/approve", 409)
        expect(admin, "POST", path + "/reject", 409, json={"reason": "Repetido"})
        assert category["id"] in {row["id"] for row in expect(assistant, "GET", "/api/categories?status=active", 200)["items"]}
        rejected = propose(assistant, prefix + " rechazo", created)
        rejected_path = f"/api/categories/{rejected['id']}"
        expect(admin, "POST", rejected_path + "/reject", 400, json={})
        result = expect(admin, "POST", rejected_path + "/reject", 200, json={"reason": "Categoría fuera del catálogo"})
        assert result["approval_status"] == "rejected" and result["is_active"] is False
        expect(admin, "POST", rejected_path + "/reject", 409, json={"reason": "Repetido"})
        copy = expect(assistant, "POST", path + "/duplicate", 201)
        created.append(copy["id"])
        assert copy["approval_status"] == "pending" and copy["is_active"] is False
        for decisions in ((True, True), (True, False), (False, False)):
            race = propose(assistant, prefix + str(decisions), created)
            concurrent_decisions(app, race["id"], (users["admin"], second_admin_id), decisions)
    print("PASS DEMO HTTP: RBAC, propuesta, aprobación/rechazo, repetición 409, producto, duplicados, filtros/exportación, 10 y 6+4.")
    print("PASS DEMO MariaDB: dos administradores, lecturas simultáneas, approve/approve, approve/reject y reject/reject: 200 + 409.")


def main():
    validate_demo()
    from app import create_app
    from app.extensions import db
    from app.models import Category, User
    from docker_support.bootstrap import engine
    target = engine()
    validate_demo_url(target.url)
    before = snapshot(target)
    assert len(before[0]) == 10, "La DEMO debe conservar sus diez categorías originales."
    app = create_app()
    created, second_admin_id = [], None
    try:
        with app.app_context():
            users = {role: user_id for role, user_id in db.session.query(User.role, User.id).filter(User.is_active.is_(True)).all()}
            suffix = uuid.uuid4().hex[:10]
            second = User(name="Administrador temporal de prueba", username="category_test_" + suffix,
                          email="category_test_" + suffix + "@example.invalid", role="admin")
            db.session.add(second)
            db.session.commit()
            second_admin_id = second.id
        verify(app, users, created, second_admin_id)
    finally:
        with app.app_context():
            db.session.rollback()
            # IDs obtenidos de respuestas 201, nunca patrones de nombres ni datos originales.
            if created:
                Category.query.filter(Category.id.in_(created)).delete(synchronize_session=False)
            if second_admin_id is not None:
                User.query.filter(User.id == second_admin_id).delete(synchronize_session=False)
            db.session.commit()
        assert snapshot(target) == before, "Cambió un registro original o el stock/movimientos."
        target.dispose()
    print("PASS DEMO cleanup: diez categorías originales intactas; productos, historial de estados y movimientos con checksums idénticos.")


if __name__ == "__main__":
    main()
