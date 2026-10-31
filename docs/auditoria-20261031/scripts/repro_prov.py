"""Reproduce los 6 hallazgos de la revisión de Compras. Solo laboratorio."""
import os, uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
assert os.environ.get("AUDIT_LAB_DB") and os.environ.get("DB_NAME") == os.environ["AUDIT_LAB_DB"], "solo el laboratorio: defina AUDIT_LAB_DB con el nombre de la base aislada"
from flask import g, session
from flask_wtf.csrf import generate_csrf
from app import create_app
from app.extensions import db
from app.models import Product, User
from app.models.purchase import PurchaseOrder, Supplier
from app.services import rbac_service, session_service

app = create_app(); app.logger.disabled = True
TODAY = datetime.now(ZoneInfo("America/Caracas")).date()
TAG = uuid.uuid4().hex[:4].upper()
app.before_request_funcs.setdefault(None, []).insert(0, lambda: (g.pop("_login_user", None), rbac_service.invalidate_cache()) and None)

with app.app_context():
    def client(username):
        user = User.query.filter_by(username=username).one()
        c = app.test_client()
        with app.test_request_context():
            session_service.open_session(user); g.pop("csrf_token", None); token = generate_csrf(); cookies = dict(session)
        with c.session_transaction() as s: s.update(cookies)
        c.environ_base["HTTP_X_CSRFTOKEN"] = token
        return c
    I, A, D = client("aud_inventario"), client("aud_asistente"), client("aud_admin")
    def show(label, r): print(f"{label}: {r.status_code} {(r.get_json() or {}).get('error', '') if r.status_code >= 300 else ''}"); return r.get_json() or {}
    prod = Product.query.filter_by(code="AUD-CAB-VI60").one()
    sup = Supplier.query.filter(Supplier.name.like("E2E Proveedor%")).one()

    order_id = PurchaseOrder.query.filter_by(order_number="OC-000004").one().id
    current = A.get(f"/api/suppliers/{sup.id}").get_json()
    print("pendiente previo:", current.get("pending_change"))
    body = {k: current[k] for k in ("name", "tax_id", "contact_name", "phone", "email", "category", "is_active") if k in current}
    old = body["name"]; body["name"] = old + " RENOMBRADO"
    show("2) proponer cambio de nombre", A.patch(f"/api/suppliers/{sup.id}", json=body))
    items = (D.get("/api/suppliers?approval_status=pending&per_page=50").get_json() or {})
    rows = items.get("items") or items.get("suppliers") or []
    print("3) en filtro pendientes:", any(r.get("id") == sup.id for r in rows), "| claves:", list(items)[:6])
    show("   aprobar cambio", D.post(f"/api/suppliers/{sup.id}/approve", json={}))
    db.session.expire_all()
    print("   nombre actual:", db.session.get(Supplier, sup.id).name)
    html = D.get(f"/purchases/{order_id}/print").get_data(as_text=True)
    print("   impresión OC-000004 nombre original:", old in html, "| nombre nuevo:", (old + " RENOMBRADO") in html)
    det = D.get(f"/api/purchases/{order_id}").get_json()
    print("   API detalle OC proveedor:", det.get("supplier", {}).get("name"))
    body["name"] = old; show("   revertir", A.patch(f"/api/suppliers/{sup.id}", json=body)); show("   aprobar reversión", D.post(f"/api/suppliers/{sup.id}/approve", json={}))
