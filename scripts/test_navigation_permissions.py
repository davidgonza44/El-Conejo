#!/usr/bin/env python3
"""Sidebar y aterrizaje por permisos efectivos. SQLite temporal; no toca la base de tesis.

Uso: python scripts/test_navigation_permissions.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from test_products_core import ADMIN_PASSWORD, check, login, setup_app  # noqa: E402

from app.extensions import db  # noqa: E402
from app.models.role import Permission, Role  # noqa: E402
from app.services import rbac_service  # noqa: E402
from app.utils.navigation import home_path, nav_allowed, settings_tab_allowed  # noqa: E402


def sidebar(html: str, marker: str = 'id="ix-sidebar"') -> str:
    at = html.find(marker)
    if at < 0:
        return ""
    start = html.rfind("<aside", 0, at + 1)
    end = html.find("</aside>", at)
    if start < 0 or end < 0:
        return ""
    return html[start:end]


def _codes(app, slug: str) -> set[str]:
    with app.app_context():
        rbac_service.ensure_seeded()
        return set(Role.query.filter_by(slug=slug).one().permission_codes)


def _denied(response) -> bool:
    return response.status_code == 403 and "No tiene permisos para acceder a esta sección." in response.get_data(as_text=True)


def _set_codes(app, slug: str, codes: set[str]) -> None:
    with app.app_context():
        rbac_service.ensure_seeded()
        role = Role.query.filter_by(slug=slug).one()
        role.permissions = Permission.query.filter(Permission.code.in_(codes)).all()
        db.session.commit()
        rbac_service.invalidate_cache()


def _cost_and_ownership(app, admin, vendedor, ids) -> None:
    from test_products_core import create_via_api

    created = create_via_api(
        admin, code="NAV-1", name="Clavo", category_id=ids["construccion"],
        current_stock=8, purchase_price="12.50", sale_price="20.00",
    )
    product_id = created.json["id"]
    vendor_item = vendedor.get(f"/api/products/{product_id}")
    admin_item = admin.get(f"/api/products/{product_id}")
    vendor_list = vendedor.get("/api/products").json["items"]
    exported = vendedor.post("/api/products/export", json={
        "format": "csv", "options": {"include_stock_prices": True, "include_description": False}, "filters": {},
    })
    export_text = exported.get_data(as_text=True)
    check("el precio de compra no sale en la respuesta del vendedor",
          vendor_item.status_code == 200 and "purchase_price" not in vendor_item.json
          and all("purchase_price" not in item for item in vendor_list)
          and admin_item.json["purchase_price"] == 12.5
          and exported.status_code == 200 and "Precio de compra" not in export_text and "Precio de venta" in export_text)
    check("vendedor no crea productos", vendedor.post("/api/products", json={
        "code": "NAV-X", "name": "No", "category_id": ids["construccion"], "purchase_price": "1", "sale_price": "2",
    }).status_code == 403)

    # Own and foreign historical identifiers are both inaccessible by default.
    for number in ("VTA-000001", "VTA-000002"):
        check(f"venta {number} bloqueada por API y URL",
              vendedor.get(f"/api/sales/{number}").status_code == 403
              and _denied(vendedor.get(f"/sales/{number}")))
    check("vendedor no registra ventas", vendedor.post("/api/sales", json={}).status_code == 403)


def _dynamic_permissions(app, inventario, vendedor, ven_nav) -> None:
    from app.models import User
    from app.models.role import Role

    before_page = vendedor.get("/reconciliation")
    before_api = vendedor.get("/api/reconciliation")
    granted = _codes(app, "vendedor") | {"reconciliation:read"}
    _set_codes(app, "vendedor", granted)
    after_nav = sidebar(vendedor.get("/products").get_data(as_text=True))
    after_page = vendedor.get("/reconciliation")
    after_api = vendedor.get("/api/reconciliation")
    check("agregar reconciliation:read abre menú, página y API",
          _denied(before_page) and before_api.status_code == 403
          and "Conciliación" not in ven_nav and "Conciliación" in after_nav
          and after_page.status_code == 200 and after_api.status_code == 200,
          f"pagina={after_page.status_code} api={after_api.status_code}")
    _set_codes(app, "vendedor", granted - {"reconciliation:read"})
    revoked_nav = sidebar(vendedor.get("/products").get_data(as_text=True))
    check("quitar reconciliation:read cierra menú, página y API",
          "Conciliación" not in revoked_nav and _denied(vendedor.get("/reconciliation"))
          and vendedor.get("/api/reconciliation").status_code == 403)

    report_api = "/api/reports/low-stock-products"
    check("inventario consulta reportes", inventario.get(report_api).status_code == 200
          and "Reportes" in sidebar(inventario.get("/dashboard").get_data(as_text=True)))
    _set_codes(app, "inventario", _codes(app, "inventario") - {"reports:read"})
    inv_after = sidebar(inventario.get("/dashboard").get_data(as_text=True))
    check("quitar reports:read oculta el menú y niega página y API",
          "Reportes" not in inv_after and _denied(inventario.get("/reports/sales"))
          and inventario.get(report_api).status_code == 403)

    base = _codes(app, "inventario")
    _set_codes(app, "inventario", base | {"settings:read"})
    check("settings:read no abre usuarios ni su API",
          inventario.get("/settings").status_code == 200
          and _denied(inventario.get("/settings", query_string={"tab": "users"}))
          and inventario.get("/api/users").status_code == 403)
    _set_codes(app, "inventario", base)

    stock_denied = vendedor.post("/api/chatbot/message", json={"message": "Productos bajo stock"})
    _set_codes(app, "vendedor", _codes(app, "vendedor") | {"inventory:read"})
    stock_allowed = vendedor.post("/api/chatbot/message", json={"message": "Productos bajo stock"})
    check("el asistente sigue inventory:read, no el nombre del rol",
          stock_denied.status_code == 403 and stock_allowed.status_code == 200
          and "current_stock" not in stock_denied.get_data(as_text=True))
    _set_codes(app, "vendedor", _codes(app, "vendedor") - {"inventory:read", "sales:read"})

    with app.app_context():
        role = Role(slug="consulta", name="Consulta", is_system=False, is_active=True)
        role.permissions = [Permission.query.filter_by(code="products:read").one()]
        reader = User(name="Consulta", email="consulta@core.test", username="core_consulta", role="consulta")
        reader.set_password("ven123")
        db.session.add_all([role, reader])
        db.session.commit()
    reader_client = app.test_client()
    login(reader_client, "core_consulta", "ven123")
    greeting = reader_client.post("/api/chatbot/message", json={"message": "Hola"})
    check("un rol distinto también consulta el asistente si tiene products:read", greeting.status_code == 200, greeting.get_data(as_text=True)[:160])

    fresh = app.test_client()
    landed = login(fresh, "core_ven", "ven123").get_json()["home"]
    sale_denied = fresh.get("/sales")
    sale_api = fresh.get("/api/sales")
    home_nav = sidebar(fresh.get(landed).get_data(as_text=True))
    check("vendedor sin sales:read no aterriza en /sales",
          landed == "/products" and _denied(sale_denied) and sale_api.status_code == 403
          and 'href="/sales"' not in home_nav and 'href="/dashboard"' not in home_nav,
          landed)


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="nav-perms-"))
    app, admin, inventario, vendedor, _ids = setup_app(temp_root)
    with app.app_context():
        rbac_service.ensure_seeded()
        check("matriz estática: admin ve Inicio y vendedor no",
              nav_allowed("admin", "dashboard") and not nav_allowed("vendedor", "dashboard"))
        check("matriz: vendedor lee productos y no compras ni configuración",
              nav_allowed("vendedor", "products") and nav_allowed("vendedor", "categories")
              and not nav_allowed("vendedor", "purchases") and not nav_allowed("vendedor", "settings"))
        check("matriz: inventario ve recepciones y no aprobaciones ni alta de venta",
              nav_allowed("inventario", "inventory_summary") and nav_allowed("inventario", "receipts")
              and not nav_allowed("inventario", "purchase_approvals") and not nav_allowed("inventario", "sales_new")
              and not settings_tab_allowed("inventario", "users"))
        check("aterrizaje por rol",
              home_path("admin") == "/dashboard" and home_path("inventario") == "/dashboard"
              and home_path("vendedor") == "/products")

    for client, username, password, expected in (
        (admin, "core_admin", ADMIN_PASSWORD, "/dashboard"),
        (inventario, "core_inv", "inv123", "/dashboard"),
        (vendedor, "core_ven", "ven123", "/products"),
    ):
        body = login(client, username, password).get_json()
        check(f"login {username} -> {expected}", body.get("home") == expected, str(body.get("home")))
        location = client.get("/login").headers.get("Location", "")
        check(f"sesión {username} redirige a {expected}", expected in location, location)

    admin_nav = sidebar(admin.get("/dashboard").get_data(as_text=True))
    admin_expected = (
        "Inicio", "Resumen", "Productos", "Categorías", "Movimientos",
        "Órdenes de compra", "Recepciones", "Pendientes de aprobación", "Conciliación",
        "Pronóstico de demanda", "Reportes", "Configuración",
    )
    missing = [piece for piece in admin_expected if piece not in admin_nav]
    check("admin ve sus módulos", not missing, ", ".join(missing))
    check("admin no ve ventas ni abre listado", 'href="/sales/list"' not in admin_nav and _denied(admin.get("/sales/list")))
    settings = admin.get("/settings", query_string={"tab": "users"}).get_data(as_text=True)
    check("admin ve usuarios y roles", "Usuarios y roles" in settings and "Seguridad" in settings)

    inv_nav = sidebar(inventario.get("/dashboard").get_data(as_text=True))
    check("inventario ve inicio, inventario, compras de lectura y predictivo",
          all(piece in inv_nav for piece in ("Inicio", "Resumen", "Productos", "Movimientos", "Órdenes de compra", "Proveedores", "Recepciones", "Pronóstico de demanda", "Reportes"))
          and "Pendientes de aprobación" not in inv_nav
          and "Conciliación" not in inv_nav and "Configuración" not in inv_nav)
    check("inventario abre recepciones y puede registrarlas",
          inventario.get("/purchases/receptions").status_code == 200
          and inventario.post("/api/purchases/1/receptions", json={}).status_code == 404)
    new_sale = inventario.get("/sales/new")
    check("inventario no abre nueva venta y la API tampoco",
          _denied(new_sale) and inventario.post("/api/sales", json={}).status_code == 403)
    check("inventario no ve ventas ni abre listado",
          'href="/sales"' not in inv_nav and _denied(inventario.get("/sales/list")))

    ven_sales = vendedor.get("/sales")
    ven_nav = sidebar(vendedor.get("/products").get_data(as_text=True))
    check("vendedor entra a productos y ventas devuelve 403", vendedor.get("/products").status_code == 200 and _denied(ven_sales))
    listed = vendedor.get("/sales/list")
    check("vendedor no entra al listado", _denied(listed) and 'href="/sales/list"' not in ven_nav)
    catalog_page = vendedor.get("/catalog")
    catalog = sidebar(catalog_page.get_data(as_text=True), "app-sidebar")
    check("sidebar legado del vendedor no muestra inicio ni movimientos",
          catalog_page.status_code == 200 and catalog.startswith("<aside")
          and 'href="/dashboard"' not in catalog and 'href="/inventory"' not in catalog
          and 'href="/products"' in catalog)
    expired = vendedor.get("/password-expired")
    check("contraseña vigente vuelve al home del vendedor", expired.status_code == 302 and "/products" in expired.headers.get("Location", ""))
    check("vendedor consulta productos y notas sin menú de ventas",
          "Productos" in ven_nav and vendedor.get("/delivery-notes").status_code == 200
          and all(f'href="{path}"' not in ven_nav for path in ("/sales", "/sales/new", "/sales/list", "/sales/quotes", "/sales/orders"))
          and 'href="/dashboard"' not in ven_nav and "Movimientos" not in ven_nav and "Órdenes de compra" not in ven_nav
          and "Recepciones" not in ven_nav and "Conciliación" not in ven_nav and "Pronóstico de demanda" not in ven_nav
          and "Configuración" not in ven_nav and "Reportes" not in ven_nav and 'href="/products"' in ven_nav)

    denied = vendedor.get("/dashboard")
    check("vendedor /dashboard sigue denegado", _denied(denied))
    check("texto de acceso denegado", "panel administrativo" not in denied.get_data(as_text=True).lower())
    check("URL manual de compras sigue en 403", _denied(vendedor.get("/purchases"))
          and vendedor.get("/api/purchases").status_code == 403)
    check("URL manual de movimientos sigue denegada", _denied(vendedor.get("/inventory"))
          and vendedor.get("/api/inventory/movements").status_code == 403)
    check("ocultar configuración no abre /settings", _denied(vendedor.get("/settings")))
    _cost_and_ownership(app, admin, vendedor, _ids)
    _dynamic_permissions(app, inventario, vendedor, ven_nav)

    from test_products_core import CHECKS_RUN, FAILURES
    print(f"\n{CHECKS_RUN} comprobaciones, {FAILURES} fallos")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
