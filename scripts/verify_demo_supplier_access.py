"""HTTP real solo en DEMO 5001. Proveedores temporales con limpieza por sus IDs.

No lee contraseñas, no cambia stock, documentos operativos ni volúmenes.
--visual espera decisiones del Administrador en el navegador sobre dos fixtures.
"""
import json
import re
import sys
import uuid
from contextlib import ExitStack
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docker_support.guard import validate_demo, validate_demo_url
from verify_demo_category_approval import authenticated, expect


def snapshot(engine):
    with engine.connect() as connection:
        from docker_support.demo import require_ready
        require_ready(connection)
        rows = connection.execute(text(
            "CHECKSUM TABLE suppliers, products, stock_movements, product_state_histories, categories, "
            "purchase_orders, purchase_order_lines, purchase_receptions, purchase_reception_lines, "
            "supplier_quotes, supplier_quote_lines, sales, sale_lines"
        )).all()
        assert all(row[1] is not None for row in rows), "No se pudieron verificar las tablas operativas."
        return rows


def assert_read_only(client, supplier_id):
    path = f"/api/suppliers/{supplier_id}"
    original = expect(client, "GET", path, 200)
    expect(client, "PATCH", path, 403, json={"name": "Edicion directa"})
    for action in ("status", "preferred"):
        expect(client, "POST", path + "/" + action, 403, json={})
    expect(client, "DELETE", path, 403, json={})
    assert expect(client, "GET", path, 200) == original


def verify_roles(clients, approved_id):
    for role, client in clients.items():
        allowed = role in ("admin", "asistente")
        expect(client, "GET", "/suppliers", 200 if allowed else 403)
        landing = "/settings" if role == "soporte_ti" else "/products"
        html = expect(client, "GET", "/suppliers" if allowed else landing, 200)
        sidebar = re.search(r'<aside[^>]*id="ix-sidebar".*?</aside>', html, re.DOTALL).group(0)
        assert ('href="/suppliers"' in sidebar) == allowed, (role, "menu")
        for path in ("/api/suppliers", f"/api/suppliers/{approved_id}", "/api/suppliers/export?format=csv"):
            expect(client, "GET", path, 200 if allowed else 403)
        if allowed:
            match = re.search(r'<script[^>]*id="sp-context"[^>]*>(.*?)</script>', html, re.DOTALL)
            context = json.loads(match.group(1))
            assert context["canWrite"] == (role == "asistente")
            assert context["canApprove"] == (role == "admin")
            for value, label in (("", "Todos"), ("pending", "Pendiente"),
                                 ("approved", "Aprobado"), ("rejected", "Rechazado")):
                assert f'<option value="{value}">{label}</option>' in html
        if role != "asistente":
            expect(client, "POST", "/api/suppliers", 403, json={})
            expect(client, "PATCH", f"/api/suppliers/{approved_id}", 403, json={})
        if role != "admin":
            for action in ("approve", "reject"):
                expect(client, "POST", f"/api/suppliers/{approved_id}/{action}", 403,
                       json={"reason": "Prueba de autorizacion"})
        print(f"PASS HTTP 5001: {role}, menu, pagina, API y separacion de permisos.", flush=True)
    assert_read_only(clients["admin"], approved_id)


def verify_workflow(clients, created, visual):
    assistant, admin = clients["asistente"], clients["admin"]
    prefix = "DEMO prueba proveedores " + uuid.uuid4().hex[:8]
    payloads = []
    for action in ("approve", "reject"):
        payload = dict(name=prefix + " " + action, tax_id="TEST-" + uuid.uuid4().hex[:16],
                       contact_name="Contacto de prueba", phone="0000000000", email="test@example.invalid",
                       category="Validacion temporal", is_active=True)
        row = expect(assistant, "POST", "/api/suppliers", 201, json=payload)
        created.append(row["id"])
        payloads.append(payload)
        assert row["approval_status"] == "pending" and row["is_active"] is False
        pending = expect(admin, "GET", "/api/suppliers?approval_status=pending&search=" + prefix, 200)
        assert row["id"] in {item["id"] for item in pending["items"]}
        assert_read_only(admin, row["id"])
    if visual:
        print(json.dumps({"visual": True, "approve_id": created[0], "reject_id": created[1], "search": prefix}), flush=True)
        input()
    else:
        expect(admin, "POST", f"/api/suppliers/{created[0]}/approve", 200, json={})
        expect(admin, "POST", f"/api/suppliers/{created[1]}/reject", 200, json={"reason": "Prueba rechazo"})
    for supplier_id, state in zip(created, ("approved", "rejected")):
        row = expect(admin, "GET", f"/api/suppliers/{supplier_id}", 200)
        assert row["approval_status"] == state and row["is_active"] == (state == "approved")
        if state == "rejected":
            assert row["rejection_reason"]
        filtered = expect(admin, "GET", "/api/suppliers?approval_status=" + state + "&search=" + prefix, 200)
        assert supplier_id in {item["id"] for item in filtered["items"]}
        expect(admin, "POST", f"/api/suppliers/{supplier_id}/approve", 409, json={})
    assert_read_only(admin, created[0])
    changed = {**payloads[0], "name": prefix + " modificacion"}
    row = expect(assistant, "PATCH", f"/api/suppliers/{created[0]}", 200, json=changed)
    assert row["approval_status"] == "pending" and row["is_active"] is False
    expect(admin, "POST", f"/api/suppliers/{created[0]}/reject", 200, json={"reason": "Rechazo de modificacion de prueba"})
    print("PASS HTTP 5001: propuesta, filtros, aprobacion, rechazo, Admin sin edicion directa, Asistente propone modificacion.", flush=True)


def main():
    if sys.argv[1:] not in ([], ["--visual"]):
        raise SystemExit("Uso: verify_demo_supplier_access.py [--visual]")
    validate_demo()
    from docker_support.bootstrap import engine
    from app import create_app
    from app.extensions import db
    from app.models import User
    from app.models.purchase import Supplier

    target = engine()
    before = snapshot(target)
    app = create_app()
    validate_demo_url(make_url(app.config["SQLALCHEMY_DATABASE_URI"]))
    with app.app_context():
        users = {role: user_id for role, user_id in db.session.query(User.role, User.id).filter(User.is_active.is_(True)).all()}
        assert set(users) == {"admin", "asistente", "inventario", "soporte_ti", "vendedor"}
        approved_id = Supplier.query.filter_by(approval_status="approved").first().id
    created = []
    try:
        with ExitStack() as stack:
            clients = {role: stack.enter_context(authenticated(app, user_id)) for role, user_id in users.items()}
            verify_roles(clients, approved_id)
            verify_workflow(clients, created, sys.argv[1:] == ["--visual"])
    finally:
        with app.app_context():
            db.session.rollback()
            if created:
                Supplier.query.filter(Supplier.id.in_(created)).delete(synchronize_session=False)
                db.session.commit()
        assert snapshot(target) == before, "Cambio una tabla operativa original."
        target.dispose()
    print("PASS cleanup DEMO: proveedores originales y 12 tablas operativas conservan sus checksums.", flush=True)


if __name__ == "__main__":
    main()
