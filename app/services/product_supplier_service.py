"""Asociaciones producto–proveedor. No escribe órdenes, stock ni el costo de referencia."""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Product
from app.models.purchase import ProductSupplier, Supplier
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.purchase_service import integer, only
from app.utils.schema import table_ready
from app.utils.catalog_currency import require_currency

CREATE_KEYS = (
    "supplier_id", "purchase_price", "purchase_price_currency", "supplier_product_code", "lead_time_days",
    "minimum_order_quantity", "is_preferred",
)
UPDATE_KEYS = (
    "purchase_price", "purchase_price_currency", "supplier_product_code", "lead_time_days",
    "minimum_order_quantity", "is_preferred", "is_active",
)
MAX_MONEY = Decimal("9999999999.99")


def ready():
    return table_ready("product_suppliers") and table_ready("suppliers") and table_ready("products")


def _require_ready():
    if not ready():
        raise ValidationError(
            "Las asociaciones producto-proveedor no están disponibles. Falta aplicar la migración.",
            status_code=503,
        )


def _blank(value):
    return value is None or value == ""


def _price(value):
    if isinstance(value, bool):
        raise ValidationError("Costo del proveedor: importe inválido.")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError("Costo del proveedor: importe inválido.")
    if not amount.is_finite() or amount < 0 or amount > MAX_MONEY:
        raise ValidationError("Costo del proveedor: importe no negativo con máximo de dos decimales.")
    return amount.quantize(Decimal("0.01"))


def _code(value):
    if _blank(value):
        return None
    text = str(value).strip()
    if len(text) > 50:
        raise ValidationError("El código del proveedor admite hasta 50 caracteres.")
    return text or None


def _whole(value, label, minimum):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value).strip()):
        raise ValidationError(f"{label}: debe ser un entero.")
    number = int(str(value).strip())
    if not minimum <= number <= 2147483647:
        raise ValidationError(f"{label}: fuera del rango permitido.")
    return number


def _optional_whole(data, key, label, minimum):
    if key not in data or _blank(data.get(key)):
        return None
    return _whole(data.get(key), label, minimum)


def _flag(data, key):
    if key not in data:
        return None
    if not isinstance(data[key], bool):
        raise ValidationError(f"{key}: seleccione sí o no.")
    return data[key]


def _product(product_id, lock=False):
    query = Product.query.filter_by(id=product_id)
    if lock:
        query = query.populate_existing().with_for_update()
    product = query.first()
    if product is None:
        raise NotFoundError("Producto no encontrado.")
    return product


def _supplier(value, lock=False):
    query = Supplier.query.filter_by(id=integer(value, "Proveedor"))
    if lock:
        query = query.populate_existing().with_for_update()
    supplier = query.first()
    if supplier is None:
        raise ValidationError("Seleccione un proveedor válido.")
    return supplier


def _locked_links(product_id):
    return db.session.execute(
        select(ProductSupplier).where(ProductSupplier.product_id == product_id)
        .order_by(ProductSupplier.id).with_for_update().execution_options(populate_existing=True)
    ).scalars().all()


def _operational(link):
    supplier = link.supplier
    return bool(link.is_preferred and link.is_active and supplier is not None and supplier.is_active)


def serialize(link):
    from app.utils.permissions import response_includes_cost

    supplier = link.supplier
    payload = {
        "id": link.id,
        "product_id": link.product_id,
        "supplier_id": link.supplier_id,
        "supplier_name": supplier.name if supplier else "",
        "supplier_active": bool(supplier is not None and supplier.is_active),
        "supplier_product_code": link.supplier_product_code or "",
        "lead_time_days": link.lead_time_days,
        "minimum_order_quantity": link.minimum_order_quantity,
        "is_preferred": bool(link.is_preferred),
        "is_active": bool(link.is_active),
        "operational_preferred": _operational(link),
    }
    if response_includes_cost():
        payload["purchase_price"] = f"{link.purchase_price:.2f}"
        payload["purchase_price_currency"] = link.purchase_price_currency
    return payload


def _set_preferred(links, link, preferred):
    if not preferred:
        link.is_preferred = False
        return
    if not link.is_active:
        raise ValidationError("Una asociación inactiva no puede ser la preferida.")
    supplier = link.supplier
    if supplier is None or not supplier.is_active:
        raise ValidationError("Un proveedor inactivo no puede marcarse como preferido.")
    for other in links:
        if other is not link and other.is_preferred:
            other.is_preferred = False
    db.session.flush()
    link.is_preferred = True


def list_for_product(product_id):
    _require_ready()
    _product(product_id)
    rows = (ProductSupplier.query.options(joinedload(ProductSupplier.supplier))
            .filter_by(product_id=product_id).order_by(ProductSupplier.id).all())
    return [serialize(row) for row in rows]


def active_suppliers():
    if not table_ready("suppliers"):
        return []
    rows = Supplier.query.filter_by(is_active=True).order_by(Supplier.name, Supplier.id).all()
    return [{"id": row.id, "name": row.name} for row in rows]


def approved_suppliers_for_products(product_ids):
    """Proveedores operativos con un vínculo activo para cada producto solicitado."""
    needed = set(product_ids)
    if not needed or not ready():
        return []
    covering = (select(ProductSupplier.supplier_id)
                .where(ProductSupplier.product_id.in_(needed), ProductSupplier.is_active.is_(True))
                .group_by(ProductSupplier.supplier_id)
                .having(func.count(func.distinct(ProductSupplier.product_id)) == len(needed)))
    rows = (Supplier.query.filter_by(is_active=True, approval_status="approved")
            .filter(Supplier.id.in_(covering)).order_by(Supplier.name, Supplier.id).all())
    return [{"id": row.id, "name": row.name} for row in rows]


def require_active_associations(supplier_id, product_ids):
    """Revalida y bloquea vínculos existentes; cotizar nunca crea asociaciones."""
    _require_ready()
    needed = set(product_ids)
    links = (ProductSupplier.query.filter(
        ProductSupplier.supplier_id == supplier_id, ProductSupplier.product_id.in_(needed))
        .order_by(ProductSupplier.id).populate_existing().with_for_update().all())
    if not needed or {link.product_id for link in links if link.is_active} != needed:
        raise ConflictError(
            "El proveedor no tiene una asociación activa con todos los productos de la cotización.")


def list_for_supplier(supplier_id):
    if not ready():
        return []
    rows = (ProductSupplier.query.options(joinedload(ProductSupplier.product), joinedload(ProductSupplier.supplier))
            .filter_by(supplier_id=supplier_id).order_by(ProductSupplier.id).all())
    from app.utils.permissions import response_includes_cost

    show_cost = response_includes_cost()
    items = []
    for row in rows:
        item = {
            "product_id": row.product_id,
            "product_code": row.product.code if row.product else "",
            "product_name": row.product.name if row.product else "",
            "supplier_product_code": row.supplier_product_code or "",
            "lead_time_days": row.lead_time_days,
            "minimum_order_quantity": row.minimum_order_quantity,
            "is_preferred": bool(row.is_preferred),
            "is_active": bool(row.is_active),
            "operational_preferred": _operational(row),
        }
        if show_cost:
            item["purchase_price"] = f"{row.purchase_price:.2f}"
            item["purchase_price_currency"] = row.purchase_price_currency
        items.append(item)
    return items


def operational_hints(product_ids):
    """Preferido operativo: vínculo activo, marcado y proveedor activo. No altera el pronóstico."""
    if not product_ids or not ready():
        return {}
    rows = (ProductSupplier.query.options(joinedload(ProductSupplier.supplier))
            .join(Supplier, ProductSupplier.supplier_id == Supplier.id)
            .filter(ProductSupplier.product_id.in_(product_ids),
                    ProductSupplier.is_preferred.is_(True),
                    ProductSupplier.is_active.is_(True),
                    Supplier.is_active.is_(True)).all())
    from app.utils.permissions import response_includes_cost

    show_cost = response_includes_cost()
    hints = {}
    for row in rows:
        hint = {
            "supplier_id": row.supplier_id,
            "supplier_name": row.supplier.name,
            "lead_time_days": row.lead_time_days,
        }
        if show_cost:
            hint["purchase_price"] = f"{row.purchase_price:.2f}"
            hint["purchase_price_currency"] = row.purchase_price_currency
        hints[row.product_id] = hint
    return hints


def _fields(data, link):
    if "purchase_price" in data or "purchase_price_currency" in data:
        if "purchase_price" not in data or "purchase_price_currency" not in data:
            raise ValidationError("Indique conjuntamente el costo del proveedor y su moneda.")
        currency = require_currency(data["purchase_price_currency"], "purchase_price_currency")
        link.purchase_price = _price(data.get("purchase_price"))
        link.purchase_price_currency = currency
    if "supplier_product_code" in data:
        link.supplier_product_code = _code(data.get("supplier_product_code"))
    if "lead_time_days" in data:
        link.lead_time_days = _optional_whole(data, "lead_time_days", "Lead time", 0)
    if "minimum_order_quantity" in data:
        link.minimum_order_quantity = _optional_whole(data, "minimum_order_quantity", "Cantidad mínima", 1)


def create_link(product_id, data):
    _require_ready()
    only(data, CREATE_KEYS)
    if "purchase_price" not in data:
        raise ValidationError("Indique el costo del proveedor.")
    currency = require_currency(data.get("purchase_price_currency"), "purchase_price_currency")
    try:
        supplier = _supplier(data.get("supplier_id"), lock=True)
        product = _product(product_id, lock=True)
        if not product.is_active:
            raise ValidationError("El producto está inactivo.")
        if not supplier.is_active:
            raise ValidationError("Seleccione un proveedor activo.")
        links = _locked_links(product.id)
        if any(row.supplier_id == supplier.id for row in links):
            raise ConflictError("Ese proveedor ya está asociado a este producto.")
        link = ProductSupplier(
            product_id=product.id, supplier_id=supplier.id, supplier=supplier,
            purchase_price=_price(data.get("purchase_price")),
            purchase_price_currency=currency,
            supplier_product_code=_code(data.get("supplier_product_code")),
            lead_time_days=_optional_whole(data, "lead_time_days", "Lead time", 0),
            minimum_order_quantity=_optional_whole(data, "minimum_order_quantity", "Cantidad mínima", 1),
            is_preferred=False, is_active=True,
        )
        db.session.add(link)
        preferred = _flag(data, "is_preferred")
        if preferred:
            _set_preferred(links, link, True)
        link.updated_at = datetime.utcnow()
        db.session.commit()
        return link
    except IntegrityError:
        db.session.rollback()
        raise ConflictError("No se pudo guardar la asociación.")
    except Exception:
        db.session.rollback()
        raise


def update_link(product_id, link_id, data):
    _require_ready()
    only(data, UPDATE_KEYS)
    if not data:
        raise ValidationError("No hay cambios para guardar.")
    try:
        identity = ProductSupplier.query.filter_by(id=link_id, product_id=product_id).first()
        if identity is None:
            raise NotFoundError("Asociación no encontrada.")
        supplier = _supplier(identity.supplier_id, lock=True)
        _product(product_id, lock=True)
        links = _locked_links(product_id)
        link = next((row for row in links if row.id == link_id), None)
        if link is None:
            raise NotFoundError("Asociación no encontrada.")
        link.supplier = supplier
        active = _flag(data, "is_active")
        preferred = _flag(data, "is_preferred")
        if active is False and preferred is True:
            raise ValidationError("Una asociación inactiva no puede ser la preferida.")
        _fields(data, link)
        if active is not None:
            link.is_active = active
            if not link.is_active:
                link.is_preferred = False
                preferred = False if preferred is None else preferred
        if preferred is not None and link.is_active:
            _set_preferred(links, link, preferred)
        link.updated_at = datetime.utcnow()
        db.session.commit()
        return link
    except IntegrityError:
        db.session.rollback()
        raise ConflictError("No se pudo guardar la asociación.")
    except Exception:
        db.session.rollback()
        raise
