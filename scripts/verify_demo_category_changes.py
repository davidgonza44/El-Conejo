"""Regresión HTTP/MariaDB exclusiva de DEMO 5001; limpia solo sus IDs temporales."""
import sys
import json
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docker_support.guard import validate_demo, validate_demo_url
from verify_demo_category_approval import authenticated, expect, snapshot


def concurrent_decisions(app, category_id, proposal_id, administrators, decisions):
    from flask_login import login_user
    from app.extensions import db
    from app.models import User, Category, CategoryChange
    from app.services import category_change_service
    from app.services.exceptions import ConflictError
    barrier = Barrier(2)
    locked = category_change_service._locked_category
    def simultaneous(key):
        barrier.wait(timeout=15)
        return locked(key)
    def decide(pair):
        user_id, approve = pair
        with app.test_request_context():
            login_user(db.session.get(User, user_id))
            try:
                category_change_service.decide_change(category_id, proposal_id, approve=approve, reason="Prueba concurrente")
                return 200, user_id, approve
            except ConflictError:
                return 409, user_id, approve
    with patch.object(category_change_service, "_locked_category", side_effect=simultaneous):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(decide, zip(administrators, decisions)))
    assert sorted(row[0] for row in results) == [200, 409], results
    winner = next(row for row in results if row[0] == 200)
    with app.app_context():
        category = db.session.get(Category, category_id)
        proposal = db.session.get(CategoryChange, proposal_id)
        assert category.name == (proposal.changes["name"] if winner[2] else proposal.previous_values["name"])
        assert proposal.reviewed_by_user_id == winner[1]
        assert proposal.status == ("approved" if winner[2] else "rejected")


def verify_change(assistant, admin, inventory, path, changes, action, users):
    before = expect(admin, "GET", path, 200)
    for actor in (admin, inventory):
        for method in ("PUT", "PATCH"):
            expect(actor, method, path, 403, json={"name": "Directo", "is_active": False})
    pending = expect(assistant, "PATCH", path, 200, json=changes)
    assert all(pending[key] == value for key, value in before.items() if key not in ("last_change", "pending_change", "has_pending_change"))
    proposal = pending["pending_change"]
    assert proposal["changes"] == changes and proposal["proposed_by_user_id"] == users["asistente"]
    expect(assistant, "PUT", path, 409, json={"name": "Alterada"})
    for actor in (assistant, inventory):
        expect(actor, "POST", path + "/approve", 403, json={"proposal_id": proposal["id"]})
    final = expect(admin, "POST", path + "/" + action, 200,
        json={"proposal_id": proposal["id"], "reason": "No autorizado"})
    for key in ("name", "description", "color", "icon", "is_active"):
        assert final[key] == (changes[key] if action == "approve" else before[key]), key
    assert final["approval_status"] == "approved"
    assert final["last_change"]["reviewed_by_user_id"] == users["admin"]
    for repeated in ("approve", "reject"):
        expect(admin, "POST", path + "/" + repeated, 409,
            json={"proposal_id": proposal["id"], "reason": "Repetida"})
    print("PASS DEMO " + action + ": valor original -> propuesta aparte -> operativo intacto -> resultado correcto; reintentos 409.")


def verify(app, users, created, second_admin):
    prefix = "Regresion categoria " + uuid.uuid4().hex[:10]
    with authenticated(app, users["asistente"]) as assistant, authenticated(app, users["admin"]) as admin, \
            authenticated(app, users["inventario"]) as inventory:
        page = expect(admin, "GET", "/categories", 200)
        assert 'id="cl-name"' not in page and 'data-cl-action="save"' not in page and 'data-cl-action="edit"' not in page
        for index, action in enumerate(("approve", "reject")):
            row = expect(assistant, "POST", "/api/categories", 201, json={"name": prefix + str(index)})
            created.append(row["id"])
            path = f"/api/categories/{row['id']}"
            expect(admin, "POST", path + "/approve", 200)
            changes = dict(name=prefix + str(index) + " nuevo", description="Descripcion propuesta", color="red", icon="hammer", is_active=False)
            verify_change(assistant, admin, inventory, path, changes, action, users)
        for index, decisions in enumerate(((True, True), (True, False), (False, False))):
            row = expect(assistant, "POST", "/api/categories", 201, json={"name": prefix + " carrera " + str(index)})
            created.append(row["id"])
            path = f"/api/categories/{row['id']}"
            expect(admin, "POST", path + "/approve", 200)
            pending = expect(assistant, "PUT", path, 200, json={"name": row["name"] + " nuevo"})
            concurrent_decisions(app, row["id"], pending["pending_change"]["id"], (users["admin"], second_admin), decisions)
        print("PASS DEMO MariaDB: approve/approve, approve/reject, reject/reject concurrentes: 200 + 409.")


def visual_fixture(app, users, created):
    """Dos decisiones visuales; las categorías originales nunca se modifican."""
    with authenticated(app, users["asistente"]) as assistant, authenticated(app, users["admin"]) as admin:
        name = "Regresion visual categorias " + uuid.uuid4().hex[:8]
        row = expect(assistant, "POST", "/api/categories", 201, json={"name": name})
        created.append(row["id"])
        path = f"/api/categories/{row['id']}"
        expect(admin, "POST", path + "/approve", 200)
        changes = dict(name=name + " propuesta", description="Descripcion propuesta", color="red", icon="hammer", is_active=False)
        expect(assistant, "PUT", path, 200, json=changes)
        print(json.dumps({"visual": "approve", "category_id": row["id"], "search": name}), flush=True)
        input()
        applied = expect(admin, "GET", path, 200)
        assert all(applied[key] == value for key, value in changes.items())
        assert applied["last_change"]["status"] == "approved"
        expect(assistant, "PATCH", path, 200, json={"description": "Descripcion para rechazar"})
        print(json.dumps({"visual": "reject", "category_id": row["id"], "search": name}), flush=True)
        input()
        rejected = expect(admin, "GET", path, 200)
        assert all(rejected[key] == value for key, value in changes.items())
        assert rejected["last_change"]["status"] == "rejected"
        print("PASS DEMO visual: aprobar aplica; rechazar conserva todos los valores operativos.", flush=True)


def main():
    if sys.argv[1:] not in ([], ["--visual"]):
        raise SystemExit("Uso: verify_demo_category_changes.py [--visual]")
    validate_demo()
    from docker_support.bootstrap import engine
    from app import create_app
    from app.extensions import db
    from app.models import Category, CategoryChange, User
    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    target = engine()
    validate_demo_url(target.url)
    before = snapshot(target)
    with target.connect() as connection:
        original = connection.execute(text("SELECT * FROM categories ORDER BY id")).all()
    app = create_app()
    validate_demo_url(make_url(app.config["SQLALCHEMY_DATABASE_URI"]))
    created, second_admin = [], None
    try:
        with app.app_context():
            users = {role: user_id for role, user_id in db.session.query(User.role, User.id).filter(User.is_active.is_(True)).all()}
            assert {"admin", "asistente", "inventario"}.issubset(users)
            suffix = uuid.uuid4().hex[:10]
            second = User(name="Admin temporal de regresion", username="category_change_" + suffix,
                          email="category_change_" + suffix + "@example.invalid", role="admin")
            db.session.add(second)
            db.session.commit()
            second_admin = second.id
        if sys.argv[1:] == ["--visual"]:
            visual_fixture(app, users, created)
        else:
            verify(app, users, created, second_admin)
    finally:
        with app.app_context():
            db.session.rollback()
            if created:
                CategoryChange.query.filter(CategoryChange.category_id.in_(created)).delete(synchronize_session=False)
                Category.query.filter(Category.id.in_(created)).delete(synchronize_session=False)
            if second_admin is not None:
                User.query.filter(User.id == second_admin).delete(synchronize_session=False)
            db.session.commit()
        assert snapshot(target) == before, "Cambió un registro original o inventario."
        with target.connect() as connection:
            assert connection.execute(text("SELECT * FROM categories ORDER BY id")).all() == original
        target.dispose()
    print("PASS DEMO cleanup: categorias originales, productos, movimientos e historial intactos.")


if __name__ == "__main__":
    main()
