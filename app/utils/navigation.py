"""Menú visible y aterrizaje tras el login, según permisos efectivos.

Cada entrada exige el mismo permiso que ya protege su ruta. Un permiso de más
muestra la opción; uno de menos la oculta. Inicio sigue el rol porque
``/dashboard`` no tiene un permiso granular: lo autorizan admin e inventario.
"""
from app.models.user import ROLE_ADMIN, ROLE_ASISTENTE, ROLE_INVENTARIO, ROLE_SOPORTE_TI, ROLE_VENDEDOR
from app.utils.permissions import (
    BACKUPS_CREATE,
    BACKUPS_MANAGE,
    BACKUPS_RESTORE,
    CUSTOMERS_READ,
    DELIVERY_NOTES_READ,
    HISTORICAL_IMPORTS_READ,
    INVENTORY_READ,
    PREDICTIONS_READ,
    PRODUCTS_READ,
    PURCHASES_APPROVE,
    PURCHASES_READ,
    RECONCILIATION_READ,
    REPLENISHMENT_READ,
    REPORTS_READ,
    ROLES_MANAGE,
    SALES_READ,
    SALES_CREATE,
    SECURITY_MANAGE,
    SETTINGS_READ,
    SETTINGS_WRITE,
    SUPPLIERS_READ,
    USERS_MANAGE,
    role_has_permission,
)

# Permisos que la ruta correspondiente ya exige. Todos son necesarios.
_GATES: dict[str, tuple[str, ...]] = {
    "inventory_summary": (INVENTORY_READ,),
    "products": (PRODUCTS_READ,),
    "categories": (PRODUCTS_READ,),
    "catalog": (PRODUCTS_READ,),
    "inventory": (INVENTORY_READ,),
    "sales": (SALES_READ,),
    "sales_list": (SALES_READ,),
    "sales_new": (SALES_READ, SALES_CREATE),
    "sales_quotes": (SALES_READ,),
    "sales_orders": (SALES_READ,),
    "sales_customers": (CUSTOMERS_READ,),
    "delivery_notes": (DELIVERY_NOTES_READ,),
    "replenishment_requests": (REPLENISHMENT_READ,),
    "purchases": (PURCHASES_READ,),
    "suppliers": (SUPPLIERS_READ,),
    "receipts": (PURCHASES_READ,),
    "purchase_approvals": (PURCHASES_READ, PURCHASES_APPROVE),
    "reconciliation": (RECONCILIATION_READ,),
    "demand_forecast": (PREDICTIONS_READ,),
    "replenishment": (PREDICTIONS_READ,),
    "critical_products": (PREDICTIONS_READ,),
    "trends": (PREDICTIONS_READ,),
    "model_accuracy": (PREDICTIONS_READ,),
    "chatbot": (PRODUCTS_READ,),
    "reports": (REPORTS_READ,),
    "settings": (SETTINGS_READ,),
    "historical_imports": (HISTORICAL_IMPORTS_READ,),
    "predictions": (PREDICTIONS_READ,),
}

_BACKUP_TAB = frozenset({BACKUPS_CREATE, BACKUPS_MANAGE, BACKUPS_RESTORE})

# Primera ruta que no devuelve 403, cuando el destino del rol no está autorizado.
_FALLBACK = (
    ("/sales", "sales"),
    ("/inventory/summary", "inventory_summary"),
    ("/products", "products"),
    ("/purchases", "purchases"),
    ("/predictive/demand", "demand_forecast"),
    ("/reports/sales", "reports"),
    ("/delivery-notes", "delivery_notes"),
    ("/sales/customers", "sales_customers"),
    ("/chatbot", "chatbot"),
    ("/settings", "settings"),
    ("/categories", "categories"),
    ("/inventory", "inventory"),
)


def _allows(role: str, *codes: str) -> bool:
    return all(role_has_permission(role, code) for code in codes)


_DEMO_WITHDRAWN = frozenset({"sales", "sales_list", "sales_new", "sales_quotes", "sales_orders"})


def nav_allowed(role: str, key: str, reference: bool = False) -> bool:
    """True si el menú puede mostrar ``key``. En referencia visual no se filtra."""
    if reference is True:
        return True
    if key in _DEMO_WITHDRAWN:
        return False
    if key == "dashboard":
        return role in (ROLE_ADMIN, ROLE_INVENTARIO, ROLE_ASISTENTE)
    if key == "chatbot" and role == ROLE_SOPORTE_TI:
        return True
    if key == "settings_users":
        return _allows(role, SETTINGS_READ) and (
            role_has_permission(role, USERS_MANAGE) or role_has_permission(role, ROLES_MANAGE)
        )
    if key == "settings_security":
        return _allows(role, SETTINGS_READ, SECURITY_MANAGE)
    needed = _GATES.get(key)
    return bool(needed) and _allows(role, *needed)


def settings_tab_allowed(role: str, key: str, reference: bool = False) -> bool:
    """Pestañas de Configuración según el permiso de cada una.

    General y Notificaciones se leen con settings:read. Seguridad pide
    security:manage. Respaldo pide crear, administrar o restaurar. Usuarios
    pide users:manage o roles:manage. El marcador de predictivo pide
    predictions:read y el de integraciones pide settings:write: no hay otra
    función detrás, y así no aparecen para quien solo lee la configuración.
    """
    if reference is True:
        return True
    if not role_has_permission(role, SETTINGS_READ):
        return False
    if key in ("general", "notifications"):
        return True
    if key == "security":
        return role_has_permission(role, SECURITY_MANAGE)
    if key == "backup":
        return any(role_has_permission(role, code) for code in _BACKUP_TAB)
    if key == "users":
        return role_has_permission(role, USERS_MANAGE) or role_has_permission(role, ROLES_MANAGE)
    if key == "predictive":
        return role_has_permission(role, PREDICTIONS_READ)
    if key == "integrations":
        return role_has_permission(role, SETTINGS_WRITE)
    return False


def visible_links(role: str, children, reference: bool = False) -> list:
    if reference is True:
        return list(children)
    return [item for item in children if nav_allowed(role, item[0])]


def home_path(role: str) -> str:
    """Ruta de aterrizaje que el backend ya autoriza para ese rol."""
    if role in (ROLE_ADMIN, ROLE_INVENTARIO, ROLE_ASISTENTE):
        return "/dashboard"
    if role == ROLE_VENDEDOR and nav_allowed(role, "sales"):
        return "/sales"
    if role == ROLE_SOPORTE_TI:
        return "/settings"
    for path, key in _FALLBACK:
        if nav_allowed(role, key):
            return path
    return "/profile"
