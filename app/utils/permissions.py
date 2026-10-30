"""Mapa de permisos por rol (autorización).

El rol define QUÉ puede hacer el usuario; la autenticación define QUIÉN es.
"""
from app.models.user import ROLE_ADMIN, ROLE_ASISTENTE, ROLE_INVENTARIO, ROLE_SOPORTE_TI, ROLE_VENDEDOR

# Permisos granulares del sistema.
PRODUCTS_READ = "products:read"
PRODUCTS_WRITE = "products:write"
PRODUCTS_VIEW_COST = "products:view_cost"
PRODUCTS_VIEW_PRICE = "products:view_price"
PRODUCTS_APPROVE = "products:approve"
SUPPLIERS_READ = "suppliers:read"
SUPPLIERS_PROPOSE = "suppliers:propose"
SUPPLIERS_APPROVE = "suppliers:approve"
REPLENISHMENT_CREATE = "replenishment:create"
REPLENISHMENT_READ = "replenishment:read"
REPLENISHMENT_PROCESS = "replenishment:process"
SUPPLIER_QUOTES_READ = "supplier_quotes:read"
SUPPLIER_QUOTES_WRITE = "supplier_quotes:write"
CATEGORIES_WRITE = "categories:write"
CATEGORIES_PROPOSE = "categories:propose"
CATEGORIES_APPROVE = "categories:approve"
INVENTORY_MOVE = "inventory:move"      # entradas, salidas y conciliación autorizada
INVENTORY_ADJUSTMENTS_CREATE = "inventory_adjustments:create"
INVENTORY_ADJUSTMENTS_APPROVE = "inventory_adjustments:approve"
INVENTORY_READ = "inventory:read"      # historial de movimientos
DELIVERY_NOTES_READ = "delivery_notes:read"
DELIVERY_NOTES_CREATE = "delivery_notes:create"
DELIVERY_NOTES_CANCEL = "delivery_notes:cancel"
REPORTS_READ = "reports:read"
USERS_MANAGE = "users:manage"
HISTORICAL_IMPORTS_READ = "historical_imports:read"
HISTORICAL_IMPORTS_EXPORT = "historical_imports:export"
HISTORICAL_IMPORTS_UPLOAD = "historical_imports:upload"
HISTORICAL_IMPORTS_REVIEW = "historical_imports:review"
HISTORICAL_IMPORTS_CONFIRM = "historical_imports:confirm"
HISTORICAL_IMPORTS_REVERT = "historical_imports:revert"
PREDICTIONS_READ = "predictions:read"
CUSTOMERS_READ = "customers:read"
CUSTOMERS_WRITE = "customers:write"
SALES_READ = "sales:read"          # ventas, cotizaciones y pedidos (un solo módulo)
SALES_WRITE = "sales:write"        # cotizaciones y pedidos: documentos sin efecto en stock
SALES_CREATE = "sales:create"      # venta ordinaria y cobro, exclusivamente vendedor
# Fuera del alcance operativo predeterminado. El catálogo de permisos y las tablas
# históricas se conservan. Admin tampoco conserva lectura: el menú ya retira el
# módulo para todos los roles, y sales:write seguiría modificando cotizaciones
# y pedidos. La DEMO también los deniega si un rol persistido aún los conserva.
WITHDRAWN_CUSTOMER_SALES = frozenset({SALES_READ, SALES_WRITE, SALES_CREATE})
SETTINGS_READ = "settings:read"    # configuración de la aplicación (solo admin)
SETTINGS_WRITE = "settings:write"
BACKUPS_CREATE = "backups:create"  # respaldo manual de la base de datos (solo admin)
BACKUPS_MANAGE = "backups:manage"  # descargar, verificar, eliminar, programar y Drive
BACKUPS_RESTORE = "backups:restore"
ROLES_MANAGE = "roles:manage"
SECURITY_MANAGE = "security:manage"  # política de contraseñas
NOTIFICATIONS_MANAGE = "notifications:manage"
PURCHASES_READ = "purchases:read"
PURCHASES_WRITE = "purchases:write"
PURCHASES_RECEIVE = "purchases:receive"
PURCHASES_CANCEL = "purchases:cancel"
PURCHASES_APPROVE = "purchases:approve"
PURCHASES_CLOSE_REQUEST = "purchases:close_request"
PURCHASES_CLOSE_APPROVE = "purchases:close_approve"
RECONCILIATION_READ = "reconciliation:read"
RECONCILIATION_CREATE = "reconciliation:create"
RECONCILIATION_RESOLVE = "reconciliation:resolve"

# Etiquetas en español para la matriz de permisos (module -> label, code -> label).
MODULE_LABELS = {
    "inventory_adjustments": "Solicitudes de ajuste",
    "purchases": "Compras", "reconciliation": "Conciliación",
    "products": "Productos", "suppliers": "Proveedores", "replenishment": "Solicitudes de reposición",
    "supplier_quotes": "Cotizaciones de proveedor",
    "categories": "Categorías", "inventory": "Inventario",
    "delivery_notes": "Notas de entrega", "reports": "Reportes", "users": "Usuarios", "roles": "Roles",
    "historical_imports": "Importación histórica", "predictions": "Análisis predictivo",
    "customers": "Clientes", "sales": "Ventas", "settings": "Configuración", "security": "Seguridad",
    "notifications": "Notificaciones", "backups": "Respaldos",
}
ACTION_LABELS = {
    "receive": "Registrar recepción",
    "approve": "Aprobar o rechazar", "resolve": "Resolver",
    "read": "Ver", "write": "Crear y editar", "view_cost": "Ver costos",
    "view_price": "Ver precios de venta", "propose": "Proponer",
    "close_request": "Solicitar cierre comercial", "close_approve": "Aprobar cierre comercial",
    "process": "Procesar",
    "move": "Registrar movimientos", "create": "Crear",
    "cancel": "Anular", "manage": "Administrar", "export": "Exportar", "upload": "Cargar archivos",
    "review": "Revisar", "confirm": "Confirmar", "revert": "Revertir", "restore": "Restaurar",
}

_ALL_PERMISSIONS = {
    INVENTORY_ADJUSTMENTS_CREATE, INVENTORY_ADJUSTMENTS_APPROVE,
    PURCHASES_READ, PURCHASES_WRITE, PURCHASES_RECEIVE, PURCHASES_CANCEL, PURCHASES_APPROVE,
    PURCHASES_CLOSE_REQUEST, PURCHASES_CLOSE_APPROVE,
    SUPPLIERS_READ, SUPPLIERS_PROPOSE, SUPPLIERS_APPROVE,
    REPLENISHMENT_CREATE, REPLENISHMENT_READ, REPLENISHMENT_PROCESS,
    SUPPLIER_QUOTES_READ, SUPPLIER_QUOTES_WRITE,
    RECONCILIATION_READ, RECONCILIATION_CREATE, RECONCILIATION_RESOLVE,
    SETTINGS_READ,
    SETTINGS_WRITE,
    BACKUPS_CREATE,
    BACKUPS_MANAGE,
    BACKUPS_RESTORE,
    ROLES_MANAGE,
    SECURITY_MANAGE,
    NOTIFICATIONS_MANAGE,
    CUSTOMERS_READ,
    CUSTOMERS_WRITE,
    SALES_READ,
    SALES_WRITE,
    SALES_CREATE,
    PRODUCTS_READ,
    PRODUCTS_WRITE,
    PRODUCTS_VIEW_COST,
    PRODUCTS_VIEW_PRICE,
    PRODUCTS_APPROVE,
    CATEGORIES_WRITE,
    CATEGORIES_PROPOSE,
    CATEGORIES_APPROVE,
    INVENTORY_MOVE,
    INVENTORY_READ,
    DELIVERY_NOTES_READ,
    DELIVERY_NOTES_CREATE,
    DELIVERY_NOTES_CANCEL,
    REPORTS_READ,
    USERS_MANAGE,
    HISTORICAL_IMPORTS_READ,
    HISTORICAL_IMPORTS_EXPORT,
    HISTORICAL_IMPORTS_UPLOAD,
    HISTORICAL_IMPORTS_REVIEW,
    HISTORICAL_IMPORTS_CONFIRM,
    HISTORICAL_IMPORTS_REVERT,
    PREDICTIONS_READ,
}

_ADMIN_EXCLUDED = {
    SALES_CREATE, PURCHASES_WRITE, PURCHASES_RECEIVE, PURCHASES_CLOSE_REQUEST,
    INVENTORY_ADJUSTMENTS_CREATE, PRODUCTS_WRITE, SUPPLIERS_PROPOSE,
    SUPPLIER_QUOTES_WRITE, REPLENISHMENT_CREATE, REPLENISHMENT_PROCESS,
    CATEGORIES_PROPOSE,
}
ROLE_PERMISSIONS: dict[str, set[str]] = {
    ROLE_ADMIN: _ALL_PERMISSIONS - _ADMIN_EXCLUDED - WITHDRAWN_CUSTOMER_SALES,
    ROLE_INVENTARIO: {
        INVENTORY_ADJUSTMENTS_CREATE,
        PURCHASES_READ,
        PURCHASES_RECEIVE,
        PRODUCTS_READ,
        INVENTORY_MOVE,
        INVENTORY_READ,
        DELIVERY_NOTES_READ,
        REPORTS_READ,
        HISTORICAL_IMPORTS_READ,
        HISTORICAL_IMPORTS_EXPORT,
        PREDICTIONS_READ,
        REPLENISHMENT_CREATE,
        REPLENISHMENT_READ,
    },
    ROLE_ASISTENTE: {
        PRODUCTS_READ, PRODUCTS_WRITE, PRODUCTS_VIEW_COST, PRODUCTS_VIEW_PRICE,
        CATEGORIES_WRITE, CATEGORIES_PROPOSE, INVENTORY_READ, PURCHASES_READ, PURCHASES_WRITE,
        PURCHASES_CLOSE_REQUEST, REPLENISHMENT_READ, REPLENISHMENT_PROCESS, SUPPLIERS_READ, SUPPLIERS_PROPOSE,
        SUPPLIER_QUOTES_READ, SUPPLIER_QUOTES_WRITE, PREDICTIONS_READ, REPORTS_READ,
    },
    ROLE_VENDEDOR: {
        PRODUCTS_READ,
        PRODUCTS_VIEW_PRICE,
        DELIVERY_NOTES_READ,
        DELIVERY_NOTES_CREATE,
        CUSTOMERS_READ,
        CUSTOMERS_WRITE,
    },
    ROLE_SOPORTE_TI: {
        SETTINGS_READ,
        SECURITY_MANAGE,
        BACKUPS_CREATE,
        BACKUPS_MANAGE,
    },
}


ALL_PERMISSIONS = frozenset(_ALL_PERMISSIONS)

# Permisos que un rol "administrador efectivo" debe conservar (protección contra bloqueo).
ADMIN_CRITICAL_PERMISSIONS = frozenset({SETTINGS_READ, SETTINGS_WRITE, USERS_MANAGE, ROLES_MANAGE})


def permission_label(code: str) -> str:
    module, _, action = code.partition(":")
    return f"{ACTION_LABELS.get(action, action)} · {MODULE_LABELS.get(module, module)}"


def role_has_permission(role: str, permission: str) -> bool:
    """RBAC dinámico (tabla roles) con respaldo estático mientras la tabla no exista."""
    from app.services import rbac_service

    if permission in rbac_service.OPERATIONAL_ROLES:
        return rbac_service.can_operate(role, permission)
    return permission in rbac_service.permissions_for(role)


def _response_allows(permission: str) -> bool:
    """Sin petición HTTP el dato se conserva. En una respuesta, se omite sin el permiso."""
    from flask import has_request_context
    from flask_login import current_user

    if not has_request_context() or not current_user.is_authenticated:
        return not has_request_context()
    return role_has_permission(current_user.role, permission)


def response_includes_cost() -> bool:
    """El costo de compra sale en la respuesta solo con products:view_cost."""
    return _response_allows(PRODUCTS_VIEW_COST)


def response_includes_sale_price() -> bool:
    """El precio de venta sale en la respuesta solo con products:view_price."""
    return _response_allows(PRODUCTS_VIEW_PRICE)
