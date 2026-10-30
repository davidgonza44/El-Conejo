"""Cáscara compartida (solo lectura): búsqueda global y alertas reales para la campana.

Búsqueda Core v1 sobre entidades existentes (productos, clientes, ventas) filtrada por los
permisos del rol; devuelve solo campos de presentación/navegación (nunca purchase_price ni
datos de usuarios). Las alertas son las mismas de Product Core (agotado / bajo stock,
exclusivas) sin estado leído/no leído: no existe persistencia de notificaciones.
"""
from sqlalchemy import or_

from app.models import Customer, Product, Sale
from app.services import product_service, settings_service
from app.services.sales_common import restrict_own_sales
from app.services.dashboard_service import _alert
from app.services.exceptions import ValidationError
from app.utils.permissions import CUSTOMERS_READ, PRODUCTS_READ, SALES_READ, role_has_permission

MIN_QUERY = 2
MAX_QUERY = 80
PER_GROUP = 5


def _term(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _like(column, term):
    return column.ilike(term, escape="\\")


def _products(term):
    rows = (Product.query.filter(Product.is_active.is_(True), or_(_like(Product.name, term), _like(Product.code, term)))
            .order_by(Product.name.asc()).limit(PER_GROUP).all())
    return [{"id": p.id, "code": p.code, "name": p.name, "stock_status": p.stock_status} for p in rows]


def _customers(term):
    rows = (Customer.query.filter(Customer.is_active.is_(True), Customer.is_walk_in.is_(False),
                                  or_(_like(Customer.name, term), _like(Customer.tax_id, term),
                                      _like(Customer.phone, term), _like(Customer.email, term)))
            .order_by(Customer.name.asc()).limit(PER_GROUP).all())
    return [{"id": c.id, "name": c.name, "tax_id": c.tax_id, "phone": c.phone} for c in rows]


def _sales(term):
    rows = (restrict_own_sales(Sale.query, Sale.seller_id).join(Customer)
            .filter(or_(_like(Sale.sale_number, term), _like(Customer.name, term)))
            .order_by(Sale.sold_at.desc(), Sale.id.desc()).limit(PER_GROUP).all())
    return [{"id": s.id, "sale_number": s.sale_number, "customer": s.customer.name, "total": f"{s.total:.2f}"} for s in rows]


_GROUPS = ((PRODUCTS_READ, "products", _products), (CUSTOMERS_READ, "customers", _customers), (SALES_READ, "sales", _sales))


def search(query, role: str) -> dict:
    """Grupos permitidos para el rol con hasta PER_GROUP coincidencias cada uno."""
    query = (query or "").strip()
    if len(query) < MIN_QUERY:
        raise ValidationError(f"La búsqueda necesita al menos {MIN_QUERY} caracteres.")
    if len(query) > MAX_QUERY:
        raise ValidationError(f"La búsqueda admite hasta {MAX_QUERY} caracteres.")
    term = _term(query)
    groups = {key: fn(term) for permission, key, fn in _GROUPS if role_has_permission(role, permission)}
    return {"query": query, "groups": groups, "total": sum(len(v) for v in groups.values())}


def alerts_enabled() -> bool:
    """Canal app + evento stock bajo/agotado (Configuración > Notificaciones / General)."""
    return settings_service.get(settings_service.APP_NOTIFICATIONS) and settings_service.get(settings_service.LOW_STOCK_ALERTS)


def alerts() -> dict:
    """Alertas reales derivadas del stock actual (reglas exclusivas de Product Core)."""
    if not alerts_enabled():
        return {"count": 0, "low_stock": 0, "out_of_stock": 0, "items": [], "disabled": True}
    summary = product_service.products_summary()
    return {"count": summary["low_stock"] + summary["out_of_stock"], "low_stock": summary["low_stock"],
            "out_of_stock": summary["out_of_stock"], "items": [_alert(p) for p in summary["inventory_alerts"]]}
