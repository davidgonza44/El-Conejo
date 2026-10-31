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

    print("\n1) Cotización de 1 unidad para una solicitud de 10")
    req = show("  solicitud 10", I.post("/api/replenishment-requests", json={"items": [{"product_id": prod.id, "quantity": 10}]}))
    show("  iniciar", A.post(f"/api/replenishment-requests/{req['id']}/start", json={}))
    q1 = show("  cotización suelta de 1 ud", A.post("/api/supplier-quotes", json={"supplier_id": sup.id, "currency": "USD", "issued_on": TODAY.isoformat(), "valid_until": "2099-01-01",
              "items": [{"product_id": prod.id, "quantity": 1, "unit_price": "2.45"}]}))
    show("  preparar OC con la cotización de 1 ud", A.post(f"/api/replenishment-requests/{req['id']}/prepare", json={"supplier_quote_id": q1.get("id")}))
    q10 = show("  cotización desde la solicitud", A.post(f"/api/replenishment-requests/{req['id']}/quotes", json={"supplier_id": sup.id, "currency": "USD", "issued_on": TODAY.isoformat(),
               "valid_until": "2099-01-01", "items": [{"product_id": prod.id, "unit_price": "2.45"}]}))
    print("  cantidad guardada en esa cotización:", [l["quantity"] for l in q10.get("items", q10.get("lines", []))])
    oc = show("  preparar OC con la cotización correcta", A.post(f"/api/replenishment-requests/{req['id']}/prepare", json={"supplier_quote_id": q10.get("id")}))
    order_id = oc.get("purchase_order", {}).get("id")
    show("  aprobar OC", D.post(f"/api/purchases/{order_id}/approve", json={}))

    print("\n2) Renombrar el proveedor tras aprobar la OC")
    current = A.get(f"/api/suppliers/{sup.id}").get_json()
    body = {k: current[k] for k in ("name", "tax_id", "contact_name", "phone", "email", "category") if k in current}
    old = body["name"]; body["name"] = old + " RENOMBRADO"
    show("  proponer cambio de nombre", A.patch(f"/api/suppliers/{sup.id}", json=body))
    print("3) ¿aparece en el filtro de pendientes?", any(r["id"] == sup.id for r in (D.get("/api/suppliers?approval_status=pending").get_json() or {}).get("items", [])))
    show("  aprobar cambio", D.post(f"/api/suppliers/{sup.id}/approve", json={}))
    db.session.expire_all()
    html = D.get(f"/purchases/{order_id}/print").get_data(as_text=True)
    print("  impresión muestra nombre original:", old in html and (old + " RENOMBRADO") not in html,
          "| snapshot:", (db.session.get(PurchaseOrder, order_id).supplier_snapshot or {}).get("name"))
    body["name"] = old; A.patch(f"/api/suppliers/{sup.id}", json=body); D.post(f"/api/suppliers/{sup.id}/approve", json={})

    print("\n4) Desactivar un proveedor aprobado")
    show("  proponer solo desactivación sin motivo", A.post(f"/api/suppliers/{sup.id}/status-proposals", json={"is_active": False}))
    show("  proponer desactivación con motivo", A.post(f"/api/suppliers/{sup.id}/status-proposals", json={"is_active": False, "reason": "Proveedor incumplió dos entregas seguidas."}))
    show("  rechazar la propuesta (para no dejarlo inactivo)", D.post(f"/api/suppliers/{sup.id}/reject", json={"reason": "Prueba de auditoría, se mantiene activo."}))

    print("\n5) Solicitud de un servicio")
    flete = Product.query.filter_by(code="E2E-FLETE-3DFAAF").one()
    show("  solicitud de servicio", I.post("/api/replenishment-requests", json={"items": [{"product_id": flete.id, "quantity": 2}]}))

    print("\n6) Recepción fechada antes de la aprobación")
    order = db.session.get(PurchaseOrder, order_id)
    line = order.lines[0]
    show("  recepción con fecha de ayer", I.post(f"/api/purchases/{order_id}/receptions", json={"received_at": (TODAY - timedelta(days=1)).isoformat(),
         "items": [{"line_id": line.id, "quantity": 10}]}))
    show("  recepción con fecha de mañana", I.post(f"/api/purchases/{order_id}/receptions", json={"received_at": (TODAY + timedelta(days=1)).isoformat(),
         "items": [{"line_id": line.id, "quantity": 10}]}))
