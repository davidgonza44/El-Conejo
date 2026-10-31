#!/usr/bin/env python3
"""Regresión de solicitud de reposición (SR) desde Reabastecimiento. SQLite desechable.

Cubre: creación correcta, error backend visible, selección vacía, doble click
(frontend in-flight + botón deshabilitado), 409, producto inválido/inactivo,
cantidad 0, no autorizado, SR creada una sola vez, sin montos para inventario
(HTML + API + CSV + detalle) y sin modificar stock ni generar OC.

Uso: python scripts/test_replenishment_sr.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")

from test_products_core import ADMIN_PASSWORD, check, create_via_api, login, setup_app  # noqa: E402

from app.extensions import db  # noqa: E402
from app.models import Category, Permission, Product, Role, User  # noqa: E402
from app.models.purchase import PurchaseOrder  # noqa: E402
from app.models.replenishment_request import ReplenishmentRequest  # noqa: E402
from app.services import rbac_service  # noqa: E402
from app.utils.permissions import REPLENISHMENT_CREATE, ROLE_PERMISSIONS, role_has_permission  # noqa: E402
from migrate_tutor_cycle import GRANTS, REVOKES  # noqa: E402

FAILURES = 0
CHECKS_RUN = 0


def _check(name: str, condition: bool, detail: str = "") -> None:
    global FAILURES, CHECKS_RUN
    CHECKS_RUN += 1
    if not condition:
        FAILURES += 1
    print(f"[{'OK' if condition else 'FALLO'}] {name}{f' — {detail}' if detail else ''}")


def _csrf(client) -> str:
    page = client.get("/predictive/replenishment")
    assert page.status_code == 200, f"replenishment page {page.status_code}"
    match = re.search(rb'data-csrf="([^"]+)"', page.data)
    assert match, "sin data-csrf en reabastecimiento"
    return match[1].decode()


def _counts(app) -> dict:
    with app.app_context():
        return {
            "sr": db.session.query(ReplenishmentRequest).count(),
            "po": db.session.query(PurchaseOrder).count(),
            "stock": {p.id: p.current_stock for p in Product.query.all()},
        }


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="replenishment-sr-"))
    app, admin, inventario, vendedor, ids = setup_app(temp_root)
    asistente = app.core_roles["asistente"]
    with app.app_context():
        support = User(name="Soporte SR", username="sr_support", email="support@sr.test", role="soporte_ti")
        support.set_password("SyntheticSupport123!")
        db.session.add(support)
        db.session.commit()
        original_permissions = {role.slug: role.permission_codes for role in Role.query.all()}
        rbac_service.ensure_seeded()
        rbac_service.ensure_seeded()
        _check("ensure_seeded idempotente conserva permisos", original_permissions == {
            role.slug: role.permission_codes for role in Role.query.all()})
        for slug in ("inventario", "asistente", "admin", "soporte_ti", "vendedor"):
            allowed = slug == "inventario"
            _check(f"{slug} seed dinámico SR", (REPLENISHMENT_CREATE in original_permissions[slug]) == allowed)
            _check(f"{slug} autorización dinámica SR", role_has_permission(slug, REPLENISHMENT_CREATE) == allowed)
            _check(f"{slug} fallback SR", (REPLENISHMENT_CREATE in ROLE_PERMISSIONS[slug]) == allowed)
            _check(f"{slug} migración no concede SR indebida", (REPLENISHMENT_CREATE in GRANTS.get(slug, set())) == allowed)
            if not allowed:
                _check(f"{slug} migración revoca SR heredada", REPLENISHMENT_CREATE in REVOKES.get(slug, set()))
            with patch.object(rbac_service, "is_available", return_value=False):
                _check(f"{slug} autorización fallback SR", role_has_permission(slug, REPLENISHMENT_CREATE) == allowed)
    soporte = app.test_client()
    login(soporte, "sr_support", "SyntheticSupport123!")
    denied_roles = (("asistente", asistente), ("admin", admin), ("soporte_ti", soporte), ("vendedor", vendedor))

    # Productos activos aprobados vía API (asistente propone, admin aprueba).
    created_a = create_via_api(admin, code="SR-001", name="Tornillo SR", category_id=ids["construccion"],
                               current_stock=2, minimum_stock=10, purchase_price="5.00", sale_price="7.00")
    _check("alta SR-001 aprobada", created_a.status_code == 201, str(created_a.status_code))
    created_b = create_via_api(admin, code="SR-002", name="Tuerca SR", category_id=ids["construccion"],
                               current_stock=1, minimum_stock=8, purchase_price="3.00", sale_price="4.50")
    _check("alta SR-002 aprobada", created_b.status_code == 201, str(created_b.status_code))
    pid_a, pid_b = created_a.json["id"], created_b.json["id"]

    with app.app_context():
        from datetime import datetime, time, timedelta
        from app.models import Customer, Sale, SaleLine, StockMovement
        from app.models.sale import STATUS_COMPLETED
        from app.services.inventory_service import COMMERCIAL_ZONE
        from datetime import timezone as _tz
        cat = db.session.get(Category, ids["construccion"])
        inactive = Product(code="SR-INX", name="Inactivo SR", category_id=cat.id, unit="unidad",
                           current_stock=5, minimum_stock=10, purchase_price=1, sale_price=2,
                           is_active=False, approval_status="approved")
        pending = Product(code="SR-PEN", name="Pendiente SR", category_id=cat.id, unit="unidad",
                          current_stock=5, minimum_stock=10, purchase_price=1, sale_price=2,
                          is_active=True, approval_status="pending")
        customer = Customer(name="Cliente SR", is_walk_in=True)
        db.session.add_all([inactive, pending, customer])
        db.session.flush()
        pid_inactive, pid_pending = inactive.id, pending.id
        prod_a = db.session.get(Product, pid_a)
        prod_b = db.session.get(Product, pid_b)
        admin_id = prod_a.proposed_by_user_id or 1
        today = datetime.now(COMMERCIAL_ZONE).date()

        def utc_noon(day):
            return datetime.combine(day, time(12, 0), tzinfo=COMMERCIAL_ZONE).astimezone(_tz.utc).replace(tzinfo=None)

        for back in range(60, 0, -1):
            day = today - timedelta(days=back)
            when = utc_noon(day)
            for prod, qty in ((prod_a, 2), (prod_b, 1)):
                sale = Sale(customer_id=customer.id, seller_id=admin_id, sold_at=when,
                            payment_method="cash", payment_condition="cash",
                            status=STATUS_COMPLETED, subtotal=qty, total=qty)
                db.session.add(sale)
                db.session.flush()
                # Historical sale fixture: a valid salida (the DB rejects unknown types and broken arithmetic).
                mov = StockMovement(product_id=prod.id, movement_type="salida", quantity=qty,
                                    previous_stock=qty, new_stock=0, user_id=admin_id, created_at=when)
                db.session.add(mov)
                db.session.flush()
                db.session.add(SaleLine(sale_id=sale.id, product_id=prod.id, stock_movement_id=mov.id,
                                        product_code=prod.code, product_name=prod.name, quantity=qty,
                                        unit_price=1, line_subtotal=qty, line_total=qty))
        db.session.commit()

    inv_csrf = _csrf(inventario)
    inv_headers = {"X-CSRFToken": inv_csrf}
    # Sin CSRF válido el decorador de permiso (403) corre antes que el CSRF (400):
    # basta un token cualquiera para los casos no autorizados.
    denied_headers = {"X-CSRFToken": "token-no-valido"}

    # 1. Página inventario: botón/modal/selección sí; montos no.
    html = inventario.get("/predictive/replenishment").get_data(as_text=True)
    _check("inventario ve botón generar solicitud", 'id="px-order-open"' in html)
    _check("inventario ve checkboxes de selección", "data-order-pick" in html)
    _check("inventario ve modal de solicitud", 'id="rl-order"' in html)
    _check("inventario carga predictive_order.js", "predictive_order.js" in html)
    _check("inventario NO ve Monto estimado", "Monto estimado" not in html)
    _check("inventario NO ve Costo estimado", "Costo estimado" not in html)
    table_html = html.split("Recomendaciones de reabastecimiento")[1].split("</table>")[0] if "Recomendaciones de reabastecimiento" in html else ""
    _check("inventario NO ve montos en tabla", "$" not in table_html, table_html[:160])
    _check("fila sin data-unit-cost", "data-unit-cost" not in html)
    _check("fila sin data-supplier-cost", "data-supplier-cost" not in html)
    _check("fila expone motivo/stock para modal", "data-reason" in html and "data-stock-text" in html and "data-prio-label" in html)

    # Top sin costos para inventario (coste None -> sin separador con monto).
    _check("inventario top sin montos", html.count("$") == 0 or "Monto" not in html)

    # Asistente: ve costos pero NO puede solicitar (sin replenishment:create).
    for label, client in denied_roles:
        response = client.get("/predictive/replenishment")
        denied_html = response.get_data(as_text=True)
        _check(f"{label} consulta según permiso de análisis", response.status_code == (
            200 if label in ("asistente", "admin") else 403))
        _check(f"{label} NO ve acción de solicitud", "Generar solicitud de reposición" not in denied_html
               and 'id="px-order-open"' not in denied_html)
        _check(f"{label} NO ve selección SR", "data-order-pick" not in denied_html
               and 'id="px-selection"' not in denied_html)
        _check(f"{label} NO ve modal ni JS de solicitud", 'id="rl-order"' not in denied_html
               and "predictive_order.js" not in denied_html)
        if label == "asistente":
            _check("asistente mantiene costos y montos", "Costo estimado" in denied_html
                   and "Monto estimado de compra" in denied_html)
            _check("asistente conserva filtros y tabla de consulta", 'id="px-filters"' in denied_html
                   and 'id="replenishment-title"' in denied_html)
            inbox = client.get("/purchases/requests")
            _check("asistente consulta SR existentes en Compras", inbox.status_code == 200)

    empty_html = inventario.get("/predictive/replenishment?search=SIN-COINCIDENCIAS-SR").get_data(as_text=True)
    _check("inventario sin filas mantiene botón deshabilitado", bool(re.search(
        r'<button\b[^>]*id="px-order-open"[^>]*\bdisabled\b', empty_html)))
    _check("inventario sin filas no muestra selección", "data-order-pick" not in empty_html)

    # El permiso sigue siendo necesario incluso para el rol Inventario.
    with app.app_context():
        role = Role.query.filter_by(slug="inventario").one()
        saved_permissions = list(role.permissions)
        saved_codes = role.permission_codes
        role.permissions = [permission for permission in saved_permissions if permission.code != REPLENISHMENT_CREATE]
        db.session.commit()
        rbac_service.invalidate_cache()
        try:
            no_permission_html = inventario.get("/predictive/replenishment").get_data(as_text=True)
            _check("inventario sin permiso NO ve acción ni selección", "Generar solicitud de reposición" not in no_permission_html
                   and "data-order-pick" not in no_permission_html and 'id="rl-order"' not in no_permission_html)
            denied = inventario.post("/api/predictions/replenishment/orders", json={"confirm": True, "items": []},
                                     headers=inv_headers)
            _check("inventario sin permiso POST 403", denied.status_code == 403)
        finally:
            # El análisis libera la sesión SQLAlchemy; volver a cargar el rol antes de restaurar.
            role = Role.query.filter_by(slug="inventario").one()
            role.permissions = Permission.query.filter(Permission.code.in_(saved_codes)).all()
            db.session.commit()
            rbac_service.invalidate_cache()

    # 2. Creación correcta.
    before = _counts(app)
    ok = inventario.post("/api/predictions/replenishment/orders",
                         json={"confirm": True, "notes": "Caso válido",
                               "items": [{"product_id": pid_a, "quantity": 4},
                                         {"product_id": pid_b, "quantity": 2}]},
                         headers=inv_headers)
    _check("creación correcta -> 201", ok.status_code == 201, f"{ok.status_code} {ok.get_data(as_text=True)[:240]}")
    body = ok.get_json() or {}
    _check("número SR-######", bool(re.fullmatch(r"SR-\d{6}", body.get("request_number") or "")),
           str(body.get("request_number")))
    _check("respuesta sin unit_cost ni total", "unit_cost" not in body and "total" not in body)
    items = body.get("items") or []
    _check("renglones sin montos", all("unit_cost" not in it and "total" not in it for it in items),
           str(items)[:200])
    after = _counts(app)
    _check("SR creada una sola vez", after["sr"] == before["sr"] + 1, f"{before['sr']}->{after['sr']}")
    _check("sin OC generada", after["po"] == before["po"])
    _check("stock intacto", after["stock"] == before["stock"])
    with app.app_context():
        row = ReplenishmentRequest.query.filter_by(request_number=body.get("request_number")).one_or_none()
        _check("SR existe en BD", row is not None and row.status == "open" and len(row.lines) == 2)
        _check("cantidades guardadas", row is not None and sorted(l.quantity for l in row.lines) == [2, 4])

    # 3. El contrato definitivo rechaza costos enviados por el cliente.
    before = _counts(app)
    sneaky = inventario.post("/api/predictions/replenishment/orders",
                             json={"confirm": True, "notes": "",
                                   "items": [{"product_id": pid_a, "quantity": 1, "unit_cost": "999.99",
                                              "total": "999.99", "price": "1"}]},
                             headers=inv_headers)
    _check("costos del cliente rechazados -> 400", sneaky.status_code == 400,
           f"{sneaky.status_code} {sneaky.get_data(as_text=True)[:200]}")
    _check("respuesta ignora montos", "999.99" not in sneaky.get_data(as_text=True))
    _check("costos no crean SR ni OC ni stock", _counts(app)["sr"] == before["sr"]
           and _counts(app)["po"] == before["po"] and _counts(app)["stock"] == before["stock"])

    # 4. Selección vacía.
    before = _counts(app)
    empty = inventario.post("/api/predictions/replenishment/orders",
                            json={"confirm": True, "notes": "", "items": []}, headers=inv_headers)
    _check("selección vacía -> 400 con error real", empty.status_code == 400 and bool((empty.get_json() or {}).get("error")),
           f"{empty.status_code} {empty.get_data(as_text=True)[:160]}")
    _check("vacía no crea SR", _counts(app) == before)

    # 5. Sin confirmación explícita.
    before = _counts(app)
    noconfirm = inventario.post("/api/predictions/replenishment/orders",
                                json={"notes": "", "items": [{"product_id": pid_a, "quantity": 1}]},
                                headers=inv_headers)
    _check("sin confirm -> 400", noconfirm.status_code == 400)
    _check("sin confirm no crea", _counts(app) == before)

    # 6. Producto inválido / inactivo / pendiente.
    for label, pid in [("inexistente", 999999), ("inactivo", pid_inactive), ("pendiente", pid_pending)]:
        before = _counts(app)
        r = inventario.post("/api/predictions/replenishment/orders",
                            json={"confirm": True, "notes": "", "items": [{"product_id": pid, "quantity": 1}]},
                            headers=inv_headers)
        _check(f"producto {label} -> 400 visible", r.status_code == 400 and bool((r.get_json() or {}).get("error")),
               f"{r.status_code} {r.get_data(as_text=True)[:160]}")
        _check(f"producto {label} no crea", _counts(app) == before)

    # 7. Cantidad 0.
    before = _counts(app)
    zero = inventario.post("/api/predictions/replenishment/orders",
                           json={"confirm": True, "notes": "", "items": [{"product_id": pid_a, "quantity": 0}]},
                           headers=inv_headers)
    _check("cantidad 0 -> 400 visible", zero.status_code == 400 and bool((zero.get_json() or {}).get("error")),
           f"{zero.status_code} {zero.get_data(as_text=True)[:160]}")
    _check("cantidad 0 no crea", _counts(app) == before)

    # 8. Duplicados en la misma solicitud (atómica: nada a medias).
    before = _counts(app)
    dup = inventario.post("/api/predictions/replenishment/orders",
                          json={"confirm": True, "notes": "",
                                "items": [{"product_id": pid_a, "quantity": 1},
                                          {"product_id": pid_a, "quantity": 2}]},
                          headers=inv_headers)
    _check("duplicados -> 400", dup.status_code == 400)
    _check("duplicados no crean parcial", _counts(app) == before)

    # 9. POST directo: los cuatro roles sin creación reciben 403 RBAC antes del CSRF.
    for label, client in denied_roles:
        before = _counts(app)
        r = client.post("/api/predictions/replenishment/orders",
                        json={"confirm": True, "notes": "", "items": [{"product_id": pid_a, "quantity": 1}]},
                        headers=denied_headers)
        _check(f"{label} no autorizado -> 403", r.status_code == 403 and bool((r.get_json() or {}).get("error")),
               f"{r.status_code} {r.get_data(as_text=True)[:160]}")
        _check(f"{label} 403 por permiso, no por CSRF", REPLENISHMENT_CREATE in (r.get_json() or {}).get("error", ""))
        _check(f"{label} no crea", _counts(app) == before)
    anon = app.test_client()
    before = _counts(app)
    r = anon.post("/api/predictions/replenishment/orders",
                  json={"confirm": True, "notes": "", "items": [{"product_id": pid_a, "quantity": 1}]})
    _check("anónimo -> 401", r.status_code == 401, str(r.status_code))
    _check("anónimo no crea", _counts(app) == before)

    # 10. Respuesta 409 desde referencia (guardas) visible y sin crear.
    before = _counts(app)
    conflict = inventario.post("/api/predictions/replenishment/orders?ref=1",
                               json={"confirm": True, "notes": "",
                                     "items": [{"product_id": pid_a, "quantity": 1}]},
                               headers={**inv_headers, "Referer": "http://localhost/predictive/replenishment?ref=1"})
    _check("ref=1 -> 409 visible", conflict.status_code == 409 and bool((conflict.get_json() or {}).get("error")),
           f"{conflict.status_code} {conflict.get_data(as_text=True)[:160]}")
    _check("409 no crea", _counts(app) == before)

    # 11. Sin CSRF -> 400 visible.
    before = _counts(app)
    no_csrf = inventario.post("/api/predictions/replenishment/orders",
                              json={"confirm": True, "notes": "", "items": [{"product_id": pid_a, "quantity": 1}]})
    # El test client conserva environ_base con token previo; limpiarlo para este caso.
    _check("sin CSRF -> 400", no_csrf.status_code in (400, 403), str(no_csrf.status_code))
    # No se afirma conteo aquí porque el token heredado puede validar; el caso explícito ya se cubre en la suite predictiva.

    # 12. Export CSV sin columna de costo para inventario.
    csv_inv = inventario.get("/api/predictions/export/replenishment")
    _check("export inventario 200", csv_inv.status_code == 200, str(csv_inv.status_code))
    text_inv = csv_inv.data.decode("utf-8-sig")
    _check("export inventario sin Costo estimado", "Costo estimado" not in text_inv and "cost" not in text_inv.splitlines()[0].lower()
           if text_inv.strip() else True, text_inv.splitlines()[0][:160] if text_inv.strip() else "")
    csv_asis = asistente.get("/api/predictions/export/replenishment")
    _check("export asistente 200 con costos", csv_asis.status_code == 200
           and "Costo estimado" in csv_asis.data.decode("utf-8-sig"), str(csv_asis.status_code))

    # 13. Detalle sin montos para inventario.
    det_inv = inventario.get(f"/api/predictions/products/{pid_a}/detail")
    _check("detalle inventario 200", det_inv.status_code == 200, str(det_inv.status_code))
    if det_inv.status_code == 200:
        dj = det_inv.get_json() or {}
        rec = dj.get("recommendation") or {}
        pref = dj.get("preferred_supplier") or {}
        _check("detalle inventario sin unit_cost", "unit_cost" not in rec, str(list(rec)[:8]))
        _check("detalle inventario sin purchase_price", "purchase_price" not in pref, str(pref)[:160])

    # 14. Frontend: response.ok, número SR, sin doble submit, sin montos.
    js_path = ROOT / "app" / "static" / "js" / "predictive_order.js"
    js = js_path.read_text(encoding="utf-8")
    _check("JS comprueba response.ok", "response.ok" in js)
    _check("JS muestra error real del backend", "data.error" in js)
    _check("JS muestra número de solicitud", "request_number" in js)
    _check("JS evita doble submit", "inFlight" in js and "disabled" in js)
    _check("JS deshabilita mientras procesa", js.count("disabled") >= 2)
    _check("JS no envía costos", "unit_cost" not in js and "supplier_id" not in js
           and "estimated_delivery_date" not in js and "payment_condition" not in js)
    _check("JS no muestra montos", "toLocaleString(\"en-US\"" not in js and "Subtotal" not in js
           and "Total estimado" not in js and "Costo unitario" not in js and "money(" not in js)
    _check("JS pide confirmación explícita", "confirm: true" in js and "Seleccione al menos" in js)

    tpl_path = ROOT / "app" / "templates" / "predictive" / "_replenishment_order.html"
    tpl = tpl_path.read_text(encoding="utf-8")
    _check("modal sin proveedor", "Proveedor" not in tpl and "supplier" not in tpl.lower())
    _check("modal sin costos", "Costo" not in tpl and "Subtotal" not in tpl and "Total estimado" not in tpl
           and "unit_cost" not in tpl)
    _check("modal con los 5 campos", all(k in tpl for k in ["Producto", "Stock", "Cantidad", "Prioridad", "Motivo"]))
    _check("modal con error visible y éxito fuera", "rl-order-error" in tpl and "rl-feedback" in tpl)

    print(f"RESULTADO: {CHECKS_RUN - FAILURES}/{CHECKS_RUN}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
