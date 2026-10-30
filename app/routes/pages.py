"""Rutas web (páginas HTML renderizadas con Jinja2).

Las rutas /api/* no se tocan: siguen devolviendo JSON puro.
Acceso al dashboard: solo admin e inventario; vendedor ve acceso denegado.
"""
from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf.csrf import generate_csrf

from datetime import datetime, timezone

from app.models.sale import PAYMENT_CONDITIONS, PAYMENT_METHODS
from app.models.user import ROLE_ADMIN, ROLE_ASISTENTE, ROLE_INVENTARIO, ROLE_SOPORTE_TI
from app.controllers import dashboard_controller, predictive_controller, report_controller, sale_controller
from app.services import (dashboard_service, inventory_service, inventory_snapshot_service, inventory_summary_service,
                          movement_export_service, order_service, sales_dashboard_service, sales_report_service)
from app.services.exceptions import NotFoundError
from app.utils import inventory_reference, predictive_reference, reports_reference, sales_reference
from app.utils.auth_decorators import forbidden_page, require_page_permission, require_page_roles
from app.utils.navigation import home_path, settings_tab_allowed
from app.utils.permissions import (
    CATEGORIES_WRITE,
    CATEGORIES_PROPOSE,
    CATEGORIES_APPROVE,
    DELIVERY_NOTES_CANCEL,
    DELIVERY_NOTES_CREATE,
    DELIVERY_NOTES_READ,
    HISTORICAL_IMPORTS_CONFIRM,
    HISTORICAL_IMPORTS_EXPORT,
    HISTORICAL_IMPORTS_READ,
    HISTORICAL_IMPORTS_REVERT,
    HISTORICAL_IMPORTS_REVIEW,
    HISTORICAL_IMPORTS_UPLOAD,
    INVENTORY_MOVE,
    INVENTORY_READ,
    PREDICTIONS_READ,
    PRODUCTS_READ,
    PRODUCTS_VIEW_COST,
    PRODUCTS_WRITE,
    REPORTS_READ,
    CUSTOMERS_READ,
    CUSTOMERS_WRITE,
    SALES_READ,
    SALES_WRITE,
    SALES_CREATE,
    SETTINGS_READ,
    role_has_permission,
)

pages_bp = Blueprint("pages", __name__)

_DASHBOARD_ROLES = (ROLE_ADMIN, ROLE_INVENTARIO, ROLE_ASISTENTE)


def _reference_mode():
    """Modo de referencia visual: solo en debug y con ``?ref=1``; nunca toca la BD."""
    return current_app.debug and request.args.get("ref") == "1"


def _home_path():
    """Tras autenticarse: admin e inventario a Inicio; vendedor a Ventas si puede."""
    return home_path(current_user.role)


@pages_bp.get("/login")
def login():
    if current_user.is_authenticated:
        return redirect(_home_path())
    return render_template("login.html")


@pages_bp.get("/forgot-password")
def forgot_password():
    return render_template("forgot_password.html")


@pages_bp.get("/reset-password")
def reset_password():
    # El token llega en el query string (?token=...) y lo lee el JS.
    return render_template("reset_password.html")


@pages_bp.get("/password-expired")
@login_required
def password_expired():
    """Contraseña expirada por política: única pantalla disponible hasta cambiarla."""
    from app.services import password_policy_service

    if not password_policy_service.is_expired(current_user):
        return redirect(_home_path())
    return render_template("password_expired.html", policy=password_policy_service.policy())


@pages_bp.get("/products")
@login_required
def products():
    # Lectura: products:read. can_write solo pinta botones; la API vuelve a exigir products:write.
    denied = require_page_permission(PRODUCTS_READ)
    if denied is not None:
        return denied
    if _reference_mode():
        return _render_inventory("inventory/products.html", inventory_reference.PRODUCTS)
    return render_template(
        "inventory/products.html",
        reference_mode=False,
        fixture=None,
        pro_shell=True,
        can_write=current_user.role == ROLE_ASISTENTE and role_has_permission(current_user.role, PRODUCTS_WRITE),
        can_view_cost=role_has_permission(current_user.role, PRODUCTS_VIEW_COST),
        commercial_today=inventory_snapshot_service.commercial_today().isoformat(),
    )


@pages_bp.get("/categories")
@login_required
def categories():
    denied = require_page_permission(PRODUCTS_READ)
    if denied is not None:
        return denied
    if _reference_mode():
        return _render_inventory("inventory/categories.html", inventory_reference.CATEGORIES)
    return render_template(
        "inventory/categories.html",
        reference_mode=False,
        fixture=None,
        pro_shell=True,
        can_write=current_user.role == "asistente" and role_has_permission(current_user.role, CATEGORIES_PROPOSE),
        can_propose=current_user.role == "asistente" and role_has_permission(current_user.role, CATEGORIES_PROPOSE),
        can_approve=current_user.role == "admin" and role_has_permission(current_user.role, CATEGORIES_APPROVE),
        can_view_cost=role_has_permission(current_user.role, PRODUCTS_VIEW_COST),
    )


@pages_bp.get("/catalog")
@login_required
def catalog():
    """Catálogo visual de solo consulta para quien puede leer productos."""
    denied = require_page_permission(PRODUCTS_READ)
    if denied is not None:
        return denied
    return render_template("catalog.html")


@pages_bp.get("/chatbot")
@login_required
def chatbot():
    """Asistente interno. El título y los datos siguen el rol."""
    from app.services.chatbot_service import assistant_title
    if current_user.role != ROLE_SOPORTE_TI and not role_has_permission(current_user.role, PRODUCTS_READ):
        return forbidden_page()
    title = assistant_title(current_user.role)
    return render_template(
        "chatbot.html",
        reference_mode=False,
        pro_shell=True,
        movements_live=True,
        assistant_title=title,
        can_view_stock=role_has_permission(current_user.role, INVENTORY_READ),
        can_view_price=role_has_permission(current_user.role, "products:view_price"),
    )


@pages_bp.get("/profile")
@login_required
def profile():
    from app.controllers.profile_controller import page
    return page()


@pages_bp.get("/inventory")
@login_required
def inventory():
    if not role_has_permission(current_user.role, INVENTORY_READ):
        return forbidden_page()
    if _reference_mode():
        return _render_inventory("inventory/movements.html", inventory_reference.MOVEMENTS)
    return render_template(
        "inventory/movements.html",
        reference_mode=False, fixture=None, pro_shell=True, movements_live=True,
        can_move=role_has_permission(current_user.role, INVENTORY_MOVE),
        can_request_adjustment=role_has_permission(current_user.role, "inventory_adjustments:create"),
        can_review_adjustment=role_has_permission(current_user.role, "inventory_adjustments:approve"),
    )


@pages_bp.get("/inventory/print")
@login_required
def inventory_print():
    """Versión imprimible (sin shell) de los movimientos filtrados; solo lectura."""
    if not role_has_permission(current_user.role, INVENTORY_READ):
        return forbidden_page()
    rows, filters, now = movement_export_service.movement_rows(request.args.to_dict())
    return render_template("inventory/movements_print.html", rows=rows, filters=filters, now=now,
                           columns=movement_export_service.COLUMNS)


# --- Módulo Inventario (shell "ix") ----------------------------------------
# Productos usa inventory/products.html en ambos modos: fixture solo si
# _reference_mode(); MySQL/API + pro_shell en modo normal. Resumen y Proveedores siguen
# mostrando "no disponible" sin servicio. Movimientos comparte plantilla en
# ambos modos; sus fixtures solo se entregan en debug con ?ref=1.


def _render_inventory(template, fixture):
    reference_mode = _reference_mode()
    return render_template(template, reference_mode=reference_mode, fixture=fixture if reference_mode else None)


@pages_bp.get("/inventory/summary")
@login_required
def inventory_summary():
    """Misma geometría en ambos modos: fixture con ?ref=1; agregado real (solo lectura) en vivo."""
    if not role_has_permission(current_user.role, INVENTORY_READ):
        return forbidden_page()
    if _reference_mode():
        return _render_inventory("inventory/summary.html", inventory_reference.SUMMARY)
    view = dashboard_controller.inventory_summary_view(inventory_summary_service.summary())
    return render_template("inventory/summary.html", reference_mode=False, fixture=None, live_view=view,
                           pro_shell=True, movements_live=True)


@pages_bp.get("/suppliers")
@login_required
def suppliers():
    if not role_has_permission(current_user.role, "suppliers:read"):
        return forbidden_page()
    from app.controllers.supplier_controller import page
    return page()


@pages_bp.get("/delivery-notes")
@login_required
def delivery_notes():
    if not role_has_permission(current_user.role, DELIVERY_NOTES_READ):
        return forbidden_page()
    return render_template(
        "delivery_notes.html",
        reference_mode=False,
        pro_shell=True,
        movements_live=True,
        can_create=role_has_permission(current_user.role, DELIVERY_NOTES_CREATE),
        can_cancel=role_has_permission(current_user.role, DELIVERY_NOTES_CANCEL),
    )


@pages_bp.get("/historical-imports")
@login_required
def historical_imports():
    if not role_has_permission(current_user.role, HISTORICAL_IMPORTS_READ):
        return forbidden_page()
    return render_template(
        "historical_imports.html",
        can_upload=role_has_permission(
            current_user.role, HISTORICAL_IMPORTS_UPLOAD
        ),
        can_review=role_has_permission(
            current_user.role, HISTORICAL_IMPORTS_REVIEW
        ),
        can_confirm=role_has_permission(
            current_user.role, HISTORICAL_IMPORTS_CONFIRM
        ),
        can_revert=role_has_permission(
            current_user.role, HISTORICAL_IMPORTS_REVERT
        ),
        can_export=role_has_permission(
            current_user.role, HISTORICAL_IMPORTS_EXPORT
        ),
        historical_csrf_token=generate_csrf(),
    )


@pages_bp.get("/predictions")
@login_required
def predictions():
    if not role_has_permission(current_user.role, PREDICTIONS_READ):
        return forbidden_page()
    return render_template("predictions.html")


@pages_bp.get("/access-denied")
def access_denied():
    return render_template("access_denied.html")


@pages_bp.get("/dashboard")
@login_required
def dashboard():
    denied = require_page_roles(*_DASHBOARD_ROLES)
    if denied is not None:
        return denied
    # Modo de referencia visual (solo en debug): la vista se dibuja con el
    # fixture determinista de dashboard_reference_data.js, sin tocar la BD.
    # Modo normal: cáscara Pro compartida con el usuario real y datos de /api/dashboard/summary.
    reference_mode = _reference_mode()
    return render_template(
        "dashboard.html",
        reference_mode=reference_mode,
        pro_shell=True,
        movements_live=not reference_mode,
        quick_actions=None if reference_mode else dashboard_service.quick_actions(current_user.role),
        pending_proposals=[] if reference_mode else dashboard_service.pending_proposals(current_user.role),
    )


@pages_bp.get("/reports")
@login_required
def reports():
    """Dashboard de Reportes (shell "ix"). La réplica visual solo existe en debug con
    ?ref=1 y usa el fixture de app/utils/reports_reference.py (no toca la BD); en modo
    normal los reportes reales siguen viviendo en Inicio (/dashboard)."""
    denied = require_page_roles(*_DASHBOARD_ROLES)
    if denied is not None:
        return denied
    if not _reference_mode():
        return redirect(url_for("pages.dashboard"))
    return render_template("reports/dashboard.html", reference_mode=True, fixture=reports_reference.REPORTS)


# --- Módulo Ventas (shell "ix") ---------------------------------------------
# Nueve pantallas de referencia (collage aprobado de Ventas). Solo en debug con ?ref=1
# reciben el fixture determinista de app/utils/sales_reference.py (no toca la BD ni crea
# ventas, clientes, cotizaciones o pedidos). En modo normal `fixture` es None: Nueva venta y
# Clientes usan la API real (Sales Core); el resto muestra el estado veraz "no disponible".


def _render_sales(template, fixture, permission=SALES_READ, **context):
    if not role_has_permission(current_user.role, permission):
        return forbidden_page()
    reference_mode = _reference_mode()
    # Cáscara Ferretería Pro también en modo normal (convención de /products, /inventory);
    # movements_live muestra el usuario real en el pie, como /inventory.
    return render_template(
        template,
        reference_mode=reference_mode,
        fixture=fixture if reference_mode else None,
        pro_shell=True,
        movements_live=not reference_mode,
        sales_menu=sales_reference.MENU,
        can_sell=role_has_permission(current_user.role, SALES_CREATE),
        can_write_sales_documents=role_has_permission(current_user.role, SALES_WRITE),
        can_write_customers=role_has_permission(current_user.role, CUSTOMERS_WRITE),
        commercial_today=inventory_snapshot_service.commercial_today().isoformat(),
        **context,
    )


@pages_bp.get("/sales")
@login_required
def sales():
    """Dashboard de Ventas: fixture con ?ref=1; en vivo, agregado real del período (GET, sin escrituras)."""
    if _reference_mode():
        return _render_sales("sales/dashboard.html", sales_reference.DASHBOARD)
    view = dashboard_controller.sales_dashboard_view(sales_dashboard_service.dashboard(request.args.to_dict()))
    return _render_sales("sales/dashboard.html", None, live_view=view)


@pages_bp.get("/sales/list")
@login_required
def sales_list():
    """Lista de ventas: fixture con ?ref=1; en vivo, ventas reales con filtros del servidor (GET)."""
    if _reference_mode():
        return _render_sales("sales/list.html", sales_reference.SALES_LIST)
    return _render_sales("sales/list.html", None, live_view=sale_controller.sales_list_view(request.args.to_dict()))


@pages_bp.get("/sales/new")
@login_required
def sales_new():
    # Fecha y vendedor los fija el servidor (UTC en BD, America/Caracas en pantalla).
    denied = require_page_permission(SALES_CREATE)
    if denied is not None:
        return denied
    return _render_sales(
        "sales/new.html", sales_reference.NEW_SALE,
        sale_date=datetime.now(inventory_service.COMMERCIAL_ZONE).strftime("%d/%m/%Y"),
        payment_methods=PAYMENT_METHODS, payment_conditions=PAYMENT_CONDITIONS,
    )


@pages_bp.get("/sales/quotes")
@login_required
def sales_quotes():
    return _render_sales("sales/quotes.html", sales_reference.QUOTES)


@pages_bp.get("/sales/orders")
@login_required
def sales_orders():
    return _render_sales("sales/orders.html", sales_reference.ORDERS)


@pages_bp.get("/sales/orders/<order_number>/print")
@login_required
def order_print(order_number):
    """Versión imprimible (sin shell) de un pedido real; solo lectura, sin efecto en stock."""
    if not role_has_permission(current_user.role, SALES_READ):
        return forbidden_page()
    try:
        order = order_service.get_order(order_number)
    except NotFoundError:
        abort(404)
    ordered_at = order.ordered_at.replace(tzinfo=timezone.utc).astimezone(inventory_service.COMMERCIAL_ZONE)
    return render_template("sales/order_print.html", order=order.to_dict(), ordered_at=ordered_at,
                           now=datetime.now(inventory_service.COMMERCIAL_ZONE))


@pages_bp.get("/sales/customers")
@login_required
def sales_customers():
    return _render_sales("sales/customers.html", sales_reference.CUSTOMERS, permission=CUSTOMERS_READ)


@pages_bp.get("/sales/reports")
@login_required
def sales_reports():
    if not role_has_permission(current_user.role, REPORTS_READ):
        return forbidden_page()
    params = {"tab": request.args.get("tab", "summary")}
    if _reference_mode():
        params["ref"] = "1"
    return redirect(url_for("pages.report_sales", **params))


@pages_bp.get("/reports/sales")
@login_required
def report_sales():
    """Reporte de ventas (5 pestañas): fixture con ?ref=1; en vivo, ventas completadas reales (GET, sin escrituras).
    ``tab=trends`` (contrato antiguo) redirige a ``tab=summary`` conservando el resto de parámetros."""
    if not role_has_permission(current_user.role, REPORTS_READ):
        return forbidden_page()
    from app.utils.report_sales_reference import reference_view
    params = request.args.to_dict()
    tab = params.get("tab", "summary")
    if tab in sales_report_service.LEGACY_TABS:
        return redirect(url_for("pages.report_sales", **{**params, "tab": sales_report_service.LEGACY_TABS[tab]}))
    if tab not in dict(sales_report_service.TABS):
        abort(404)
    reference_mode = _reference_mode()
    view = reference_view(tab) if reference_mode else report_controller.sales_report_view(params)
    # Cáscara Ferretería Pro compartida también en vivo (convención de /dashboard y /sales).
    return render_template("reports/sales.html", reference_mode=reference_mode, view=view,
                           pro_shell=True, movements_live=not reference_mode)


def _sale_fixture(sale_number, fixture):
    """En modo referencia solo existe la venta del fixture; cualquier otro número es 404."""
    if _reference_mode() and sale_number != sales_reference.SALE_NUMBER:
        abort(404)
    return fixture


@pages_bp.get("/sales/<sale_number>")
@login_required
def sale_detail(sale_number):
    """Detalle de venta: fixture con ?ref=1; en vivo, la venta real (404 si no existe)."""
    if _reference_mode():
        return _render_sales("sales/detail.html", _sale_fixture(sale_number, sales_reference.SALE_DETAIL))
    if not role_has_permission(current_user.role, SALES_READ):
        return forbidden_page()
    try:
        view = sale_controller.sale_detail_view(sale_number)
    except NotFoundError:
        abort(404)
    return _render_sales("sales/detail.html", None, live_view=view)


@pages_bp.get("/sales/<sale_number>/print")
@login_required
def sale_print(sale_number):
    return _render_sales("sales/print.html", _sale_fixture(sale_number, sales_reference.SALE_PRINT))


# --- Módulo Análisis predictivo -------------------------------------------
# Cinco destinos con el shell "ix". Solo en debug con ?ref=1 reciben el fixture
# determinista de app/utils/predictive_reference.py (cero escrituras). En LIVE,
# live_builder calcula la vista real o el estado honesto "sin historial".


def _render_predictive(template, fixture, live_builder, **context):
    """Fixture solo con ?ref=1 (sin BD); en vivo, ``live_builder(args)`` calcula la vista real."""
    if not role_has_permission(current_user.role, PREDICTIONS_READ):
        return forbidden_page()
    if request.args.get("ref") == "1" and not current_app.debug:
        abort(404)
    reference_mode = _reference_mode()
    live_view = None if reference_mode else live_builder(request.args.to_dict())
    return render_template(
        template,
        reference_mode=reference_mode,
        fixture=fixture if reference_mode else None,
        live_view=live_view,
        pro_shell=True,
        movements_live=not reference_mode,
        **context,
    )


@pages_bp.get("/predictive/demand")
@login_required
def demand_forecast():
    return _render_predictive("predictive/demand_forecast.html", predictive_reference.FORECAST,
                              predictive_controller.demand_view)


@pages_bp.get("/predictive/replenishment")
@login_required
def replenishment():
    return _render_predictive("predictive/replenishment.html", predictive_reference.REPLENISHMENT,
                              predictive_controller.replenishment_view)


@pages_bp.get("/predictive/critical")
@login_required
def critical_products():
    return _render_predictive("predictive/critical_products.html", predictive_reference.CRITICAL,
                              predictive_controller.critical_view)


@pages_bp.get("/predictive/trends")
@login_required
def trends():
    state = predictive_reference.trends_state(request.args.get("trend", ""))
    return _render_predictive("predictive/trends.html", predictive_reference.TRENDS,
                              predictive_controller.trends_view, trend_state=state)


@pages_bp.get("/predictive/accuracy")
@login_required
def model_accuracy():
    return _render_predictive("predictive/model_accuracy.html", predictive_reference.ACCURACY,
                              predictive_controller.accuracy_view)



@pages_bp.get("/settings")
@login_required
def settings():
    """Configuración: referencia (?ref=1) solo con fixture; en vivo, datos reales del controlador."""
    if not role_has_permission(current_user.role, SETTINGS_READ):
        return forbidden_page()
    from app.utils.settings_reference import SETTINGS, TABS

    tab = request.args.get("tab", "general")
    if tab not in dict(TABS):
        abort(404)
    if not _reference_mode() and not settings_tab_allowed(current_user.role, tab):
        return forbidden_page()
    if _reference_mode():
        return render_template("settings/page.html", tab=tab, tabs=TABS, reference_mode=True, fixture=SETTINGS, live=None)
    from app.controllers import settings_controller

    return render_template("settings/page.html", tab=tab, tabs=TABS, reference_mode=False, fixture=None,
                           live=settings_controller.page_context(tab), pro_shell=True, movements_live=True)


@pages_bp.get("/reports/sales/history")
@login_required
def report_sales_history():
    denied = require_page_permission(REPORTS_READ)
    if denied is not None:
        return denied
    from app.controllers import sales_history_controller
    return sales_history_controller.page()


@pages_bp.get("/reports/sales/history/<kind>/<key>")
@login_required
def report_sales_history_detail(kind, key):
    denied = require_page_permission(REPORTS_READ)
    if denied is not None:
        return denied
    from app.controllers import sales_history_controller
    return sales_history_controller.page(kind, key)
