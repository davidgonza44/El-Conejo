"""RBAC dinámico: permisos efectivos por rol, CRUD de roles y protección contra bloqueo del administrador.

Mientras no existan las tablas (scripts/migrate_settings_full.py) rige el mapa estático
``ROLE_PERMISSIONS``; con tablas, la base de datos es la autoridad y se cachea por petición.
"""
import os
import re

from flask import g, has_app_context
from sqlalchemy import func

from app.extensions import db
from app.models import Permission, Role, User
from app.models.user import ROLE_ADMIN, ROLES
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.utils.permissions import (
    ADMIN_CRITICAL_PERMISSIONS, ALL_PERMISSIONS, ROLE_PERMISSIONS, WITHDRAWN_CUSTOMER_SALES, permission_label,
)
from app.utils.schema import table_ready

_SLUG_RE = re.compile(r"[^a-z0-9_]+")
_NAME_MAX, _DESCRIPTION_MAX, _SLUG_MAX = 60, 255, 20
SYSTEM_ROLE_NAMES = {
    "admin": "Administrador", "inventario": "Inventario", "asistente": "Asistente administrativo",
    "vendedor": "Vendedor", "soporte_ti": "Soporte TI",
}
SYSTEM_ROLE_DESCRIPTIONS = {
    "admin": "Administra usuarios, roles y configuración; aprueba productos, proveedores, órdenes y cierres comerciales sin crear la operación que aprueba.",
    "inventario": "Stock, solicitudes de reposición sin montos y recepción física de órdenes aprobadas.",
    "asistente": "Propone productos y proveedores, cotiza y prepara órdenes. No aprueba sus operaciones.",
    "vendedor": "Consulta productos y precios; registra clientes y notas de entrega.",
    "soporte_ti": "Configuración técnica, sesiones propias y respaldos, sin operación comercial.",
}


def is_available() -> bool:
    return table_ready(Role.__tablename__) and table_ready(Permission.__tablename__)


# Business separation is an additional deny-only constraint, never a grant.
# Persisted RBAC must still explicitly authorize the operation.
OPERATIONAL_ROLES = {
    "sales:create": "vendedor",
    "purchases:write": "asistente",
    "purchases:receive": "inventario",
    "purchases:approve": "admin",
    "purchases:close_request": "asistente",
    "purchases:close_approve": "admin",
    "inventory_adjustments:create": "inventario",
    "inventory_adjustments:approve": "admin",
    "products:write": "asistente",
    "products:approve": "admin",
    "suppliers:propose": "asistente",
    "suppliers:approve": "admin",
    "categories:propose": "asistente",
    "categories:approve": "admin",
    "replenishment:create": "inventario",
    "replenishment:process": "asistente",
    "supplier_quotes:write": "asistente",
}

# Reading purchase orders for physical reception does not authorize supplier administration.
_RESOURCE_READ_ROLES = {
    "suppliers:read": frozenset({"admin", "asistente"}),
    "replenishment:read": frozenset({"admin", "asistente", "inventario"}),
    "supplier_quotes:read": frozenset({"admin", "asistente"}),
}


def can_operate(role_slug: str, permission: str) -> bool:
    return role_slug == OPERATIONAL_ROLES[permission] and permission in permissions_for(role_slug)


def require_operational_permission(permission: str) -> None:
    from flask_login import current_user

    if not current_user.is_authenticated or not can_operate(current_user.role, permission):
        raise ValidationError("Su rol no puede realizar esta operación.", status_code=403)


def _cache() -> dict | None:
    if not has_app_context():
        return None
    if "_rbac_cache" not in g:
        g._rbac_cache = {}
    return g._rbac_cache


def invalidate_cache() -> None:
    if has_app_context() and "_rbac_cache" in g:
        g._rbac_cache = {}


def customer_sales_withdrawn() -> bool:
    """True solo en la DEMO explícita, o en la prueba que activa el mismo cierre.

    El mapa estático sigue otorgando ventas fuera de la DEMO. Aquí se niegan
    venta ordinaria, listado, cotización de cliente y pedido de cliente.
    """
    if os.environ.get("DEMO_MODE") == "true" and os.environ.get("DOCKER_ENV_KIND") == "demo":
        return True
    from flask import current_app

    return bool(has_app_context() and current_app.config.get("DEMO_WITHDRAW_CUSTOMER_SALES") is True)


_WITHDRAWN_PAGES = frozenset({"/sales", "/sales/list", "/sales/new", "/sales/quotes", "/sales/orders"})
_KEPT_SALES_TAILS = frozenset({"customers", "reports"})


def withdrawn_customer_sales_path(path: str) -> bool:
    """Páginas y APIs del módulo retirado. Clientes, reportes y cotización de proveedor quedan fuera."""
    path = (path or "").rstrip("/") or "/"
    if path in _WITHDRAWN_PAGES or path.startswith("/sales/orders/"):
        return True
    if path.startswith("/api/sales") or path.startswith("/api/quotes") or path.startswith("/api/orders"):
        return True
    if path.startswith("/sales/"):
        tail = path[len("/sales/"):]
        return "/" not in tail and tail not in _KEPT_SALES_TAILS
    return False


def block_withdrawn_customer_sales_request():
    """403 si hay sesión. Sin sesión, la ruta sigue su 401 o su redirección al login."""
    if not customer_sales_withdrawn():
        return None
    from flask import jsonify, request
    from flask_login import current_user

    if not withdrawn_customer_sales_path(request.path) or not current_user.is_authenticated:
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "Acceso denegado: el módulo comercial de cliente está fuera del alcance operativo de la DEMO."}), 403
    from app.utils.auth_decorators import forbidden_page

    return forbidden_page()


def permissions_for(role_slug: str) -> frozenset[str]:
    """Permisos efectivos del rol. Rol inactivo o inexistente => ningún permiso."""
    if not is_available():
        # Sin tablas no hay caché: el mapa estático tiene que poder sustituirse en la misma petición.
        result = frozenset(ROLE_PERMISSIONS.get(role_slug, set()))
    else:
        cache = _cache()
        if cache is not None and role_slug in cache:
            result = cache[role_slug]
        else:
            role = Role.query.filter_by(slug=role_slug).first()
            if role is None or not role.is_active:
                result = frozenset()  # Existing tables are authoritative, including missing roles.
            else:
                result = frozenset(role.permission_codes)
            if cache is not None:
                cache[role_slug] = result
    if customer_sales_withdrawn():
        result = result - WITHDRAWN_CUSTOMER_SALES
    result = result - {code for code, allowed in _RESOURCE_READ_ROLES.items() if role_slug not in allowed}
    return result


def ensure_seeded() -> None:
    """Siembra catálogo y roles del sistema si las tablas existen pero están vacías (idempotente)."""
    if not is_available():
        return
    existing = {p.code: p for p in Permission.query.all()}
    for code in sorted(ALL_PERMISSIONS - set(existing)):
        permission = Permission(code=code, module=code.split(":")[0], label=permission_label(code))
        db.session.add(permission)
        existing[code] = permission
    for slug in ROLES:
        role = Role.query.filter_by(slug=slug).first()
        if role is None:
            role = Role(slug=slug, name=SYSTEM_ROLE_NAMES[slug], description=SYSTEM_ROLE_DESCRIPTIONS[slug],
                        is_system=True, is_active=True)
            codes = set(ROLE_PERMISSIONS[slug])
            if customer_sales_withdrawn():
                codes -= WITHDRAWN_CUSTOMER_SALES
            role.permissions = [existing[code] for code in sorted(codes)]
            db.session.add(role)
    db.session.commit()
    invalidate_cache()


def revoke_withdrawn_customer_sales() -> int:
    """Quita del RBAC persistido los tres permisos retirados, en todos los roles.

    No borra el catálogo, las ventas, las cotizaciones, los pedidos ni las migraciones.
    Admin queda incluido: no hay excepción de lectura histórica.
    """
    if not customer_sales_withdrawn() or not is_available():
        return 0
    removed = 0
    for role in Role.query.all():
        kept = [permission for permission in role.permissions if permission.code not in WITHDRAWN_CUSTOMER_SALES]
        removed += len(role.permissions) - len(kept)
        if len(kept) != len(role.permissions):
            role.permissions = kept
    if removed:
        db.session.commit()
    invalidate_cache()
    return removed


# ---- consultas ----
def role_slugs() -> list[str]:
    if not is_available():
        return list(ROLES)
    return [slug for (slug,) in db.session.query(Role.slug).filter(Role.is_active.is_(True)).order_by(Role.id)]


def role_labels() -> dict[str, str]:
    if not is_available():
        return dict(SYSTEM_ROLE_NAMES)
    return {slug: name for slug, name in db.session.query(Role.slug, Role.name)}


def validate_role_slug(slug: str, *, allow_inactive: bool = False) -> str:
    """Slug de rol asignable a un usuario: debe existir y estar activo."""
    slug = (slug or "").strip().lower()
    if not is_available():
        if slug not in ROLES:
            raise ValidationError(f"Rol inválido: '{slug}'. Valores permitidos: {', '.join(ROLES)}.")
        return slug
    role = Role.query.filter_by(slug=slug).first()
    if role is None and slug in ROLES:  # tablas creadas pero aún sin sembrar
        ensure_seeded()
        role = Role.query.filter_by(slug=slug).first()
    if role is None:
        raise ValidationError(f"Rol inválido: '{slug}'.")
    if not role.is_active and not allow_inactive:
        raise ValidationError(f"El rol '{role.name}' está desactivado y no puede asignarse.")
    return slug


def _user_counts() -> dict[str, int]:
    rows = db.session.query(User.role, func.count(User.id)).group_by(User.role).all()
    return {slug: count for slug, count in rows}


def list_roles() -> list[dict]:
    counts = _user_counts()
    if not is_available():
        return [{"id": None, "slug": slug, "name": SYSTEM_ROLE_NAMES[slug], "description": SYSTEM_ROLE_DESCRIPTIONS[slug],
                 "is_system": True, "is_active": True, "permissions": sorted(ROLE_PERMISSIONS[slug]),
                 "user_count": counts.get(slug, 0), "created_at": None, "updated_at": None} for slug in ROLES]
    ensure_seeded()
    return [role.to_dict(counts.get(role.slug, 0)) for role in Role.query.order_by(Role.is_system.desc(), Role.id).all()]


def list_permissions() -> list[dict]:
    if not is_available():
        return [{"code": c, "module": c.split(":")[0], "label": permission_label(c)} for c in sorted(ALL_PERMISSIONS)]
    ensure_seeded()
    return [p.to_dict() for p in Permission.query.order_by(Permission.module, Permission.code).all()]


def get_role_or_404(role_id: int) -> Role:
    _require_tables()
    role = db.session.get(Role, role_id)
    if role is None:
        raise NotFoundError(f"El rol con id {role_id} no existe.")
    return role


# ---- protección contra bloqueo del administrador ----
def _is_admin_capable(permissions: set[str] | frozenset[str]) -> bool:
    return ADMIN_CRITICAL_PERMISSIONS <= set(permissions)


def _capable_slugs(overrides: dict[str, tuple[bool, set[str]]] | None = None) -> set[str]:
    """Slugs de roles activos que otorgan capacidad administrativa, aplicando cambios hipotéticos."""
    overrides = overrides or {}
    if not is_available():
        base = {slug for slug in ROLES if _is_admin_capable(ROLE_PERMISSIONS[slug])}
    else:
        base = {r.slug for r in Role.query.all() if r.is_active and _is_admin_capable(r.permission_codes)}
    for slug, (active, permissions) in overrides.items():
        base.discard(slug)
        if active and _is_admin_capable(permissions):
            base.add(slug)
    return base


def _active_admins(capable: set[str], *, exclude_user_id: int | None = None) -> int:
    if not capable:
        return 0
    query = User.query.filter(User.is_active.is_(True), User.role.in_(capable))
    if exclude_user_id is not None:
        query = query.filter(User.id != exclude_user_id)
    return query.count()


def guard_last_admin(user: User) -> None:
    """Rechaza degradar/desactivar al único usuario activo con capacidad administrativa."""
    capable = _capable_slugs()
    if not user.is_active or user.role not in capable:
        return
    if _active_admins(capable, exclude_user_id=user.id) == 0:
        raise ConflictError("No se puede degradar ni desactivar al único administrador activo.")


def _guard_role_change(role: Role, active: bool, permissions: set[str]) -> None:
    """El cambio no puede dejar el sistema sin ningún administrador efectivo."""
    if role.slug == ROLE_ADMIN:
        if not active:
            raise ConflictError("El rol Administrador del sistema no puede desactivarse.")
        if not _is_admin_capable(permissions):
            raise ConflictError("El rol Administrador debe conservar los permisos de configuración, usuarios y roles.")
    if _active_admins(_capable_slugs({role.slug: (active, permissions)})) == 0:
        raise ConflictError("El cambio dejaría el sistema sin ningún administrador activo.")


# ---- CRUD ----
def _require_tables() -> None:
    if not is_available():
        raise ValidationError("Las tablas de roles no existen. Ejecute scripts/migrate_settings_full.py.", status_code=503)


def _clean_name(value, current: Role | None = None) -> str:
    name = (value or "").strip() if isinstance(value, str) else ""
    if not name:
        raise ValidationError("El nombre del rol es obligatorio.")
    if len(name) > _NAME_MAX:
        raise ValidationError(f"El nombre del rol no puede exceder {_NAME_MAX} caracteres.")
    clash = Role.query.filter(func.lower(Role.name) == name.lower()).first()
    if clash is not None and (current is None or clash.id != current.id):
        raise ConflictError(f"Ya existe un rol llamado '{clash.name}'.")
    return name


def _clean_description(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("La descripción debe ser texto.")
    value = value.strip()
    if len(value) > _DESCRIPTION_MAX:
        raise ValidationError(f"La descripción no puede exceder {_DESCRIPTION_MAX} caracteres.")
    return value or None


def _clean_permissions(value) -> set[str]:
    if not isinstance(value, list):
        raise ValidationError("Los permisos deben enviarse como lista de códigos.")
    codes = {str(code).strip() for code in value}
    unknown = sorted(codes - ALL_PERMISSIONS)
    if unknown:
        raise ValidationError(f"Permisos desconocidos: {', '.join(unknown)}.")
    if customer_sales_withdrawn() and codes & WITHDRAWN_CUSTOMER_SALES:
        raise ValidationError("Esos permisos están fuera del alcance operativo de la DEMO.", status_code=403)
    return codes


def _clean_bool(value, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"El campo '{field}' debe ser verdadero o falso.")
    return value


def _unique_slug(name: str) -> str:
    base = _SLUG_RE.sub("_", name.lower().replace("-", "_")).strip("_")[:_SLUG_MAX - 3] or "rol"
    candidate, suffix = base, 1
    while Role.query.filter_by(slug=candidate).first() is not None:
        suffix += 1
        candidate = f"{base}_{suffix}"
    return candidate


def _permission_rows(codes: set[str]) -> list[Permission]:
    ensure_seeded()
    return Permission.query.filter(Permission.code.in_(codes)).all() if codes else []


def create_role(data: dict, actor: User | None = None) -> Role:
    _require_tables()
    ensure_seeded()
    name = _clean_name(data.get("name"))
    permissions = _clean_permissions(data.get("permissions", []))
    if not permissions:
        raise ValidationError("Seleccione al menos un permiso para el rol.")
    role = Role(slug=_unique_slug(name), name=name, description=_clean_description(data.get("description")),
                is_system=False, is_active=_clean_bool(data.get("is_active", True), "is_active"))
    role.permissions = _permission_rows(permissions)
    db.session.add(role)
    db.session.commit()
    invalidate_cache()
    _notify_security(f"Rol creado: {role.name}", f"Se creó el rol '{role.name}' con {len(permissions)} permisos.", actor)
    return role


def update_role(role_id: int, data: dict, actor: User | None = None) -> Role:
    role = get_role_or_404(role_id)
    active = _clean_bool(data["is_active"], "is_active") if "is_active" in data else role.is_active
    permissions = _clean_permissions(data["permissions"]) if "permissions" in data else role.permission_codes
    if "permissions" in data and not permissions:
        raise ValidationError("Seleccione al menos un permiso para el rol.")
    _guard_role_change(role, active, permissions)
    changes = []
    if "name" in data:
        role.name = _clean_name(data.get("name"), current=role)
        changes.append("nombre")
    if "description" in data:
        role.description = _clean_description(data.get("description"))
    if "is_active" in data and active != role.is_active:
        role.is_active = active
        changes.append("estado")
    if "permissions" in data and permissions != role.permission_codes:
        role.permissions = _permission_rows(permissions)
        changes.append("permisos")
    db.session.commit()
    invalidate_cache()
    if changes:
        _notify_security(f"Rol modificado: {role.name}", f"Se actualizó {', '.join(changes)} del rol '{role.name}'.", actor)
    return role


def duplicate_role(role_id: int, actor: User | None = None) -> Role:
    source = get_role_or_404(role_id)
    base = f"Copia de {source.name}"[:_NAME_MAX]
    name, suffix = base, 1
    while Role.query.filter(func.lower(Role.name) == name.lower()).first() is not None:
        suffix += 1
        name = f"{base[:_NAME_MAX - 4]} {suffix}"
    role = Role(slug=_unique_slug(name), name=name, description=source.description, is_system=False, is_active=True)
    copied = list(source.permissions)
    if customer_sales_withdrawn():
        copied = [permission for permission in copied if permission.code not in WITHDRAWN_CUSTOMER_SALES]
    role.permissions = copied
    db.session.add(role)
    db.session.commit()
    invalidate_cache()
    _notify_security(f"Rol duplicado: {role.name}", f"Se duplicó el rol '{source.name}' como '{role.name}'.", actor)
    return role


def delete_role(role_id: int, actor: User | None = None) -> None:
    role = get_role_or_404(role_id)
    if role.is_system:
        raise ConflictError("Los roles del sistema no pueden eliminarse.")
    assigned = User.query.filter(User.role == role.slug).count()
    if assigned:
        raise ConflictError(f"El rol '{role.name}' tiene {assigned} usuario(s) asignado(s); reasígnelos antes de eliminarlo.")
    name = role.name
    db.session.delete(role)
    db.session.commit()
    invalidate_cache()
    _notify_security(f"Rol eliminado: {name}", f"Se eliminó el rol '{name}'.", actor)


def _notify_security(title: str, message: str, actor: User | None) -> None:
    from app.services import notification_service

    notification_service.security_event(title, message, actor=actor, dedupe_key=None)
