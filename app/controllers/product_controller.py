"""Controlador de productos: traduce HTTP <-> servicio."""
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
import json

from flask import jsonify, request

from app.controllers import attachment_response
from app.services import (
    product_export_service,
    product_history_service,
    product_image_service,
    product_service,
    product_supplier_service,
    product_change_service,
)
from app.services.exceptions import ValidationError


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        raise ValidationError("El cuerpo de la petición debe ser un JSON válido.")
    return data


def _truthy(name: str) -> bool:
    return request.args.get(name, "").lower() in ("1", "true", "si")


def _optional_int(name: str, minimum: int | None = None) -> int | None:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"El parámetro '{name}' debe ser un número entero.")
    if minimum is not None and value < minimum:
        raise ValidationError(f"El parámetro '{name}' debe ser mayor o igual a {minimum}.")
    return value


def _optional_price(name: str) -> Decimal | None:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise ValidationError(f"El parámetro '{name}' debe ser un número.")
    if value < 0:
        raise ValidationError(f"El parámetro '{name}' no puede ser negativo.")
    return value


def _optional_date(name: str, end_of_day: bool = False) -> datetime | None:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        raise ValidationError(f"El parámetro '{name}' debe ser una fecha (YYYY-MM-DD).")
    if end_of_day:
        return datetime.combine(day, time.max)
    return datetime.combine(day, time.min)


def _items_payload(result: dict) -> dict:
    pending = product_change_service.pending_product_ids([item.id for item in result["items"]])
    payload = {
        "count": result["total"],
        "items": [dict(item.to_dict(), has_pending_change=item.id in pending) for item in result["items"]],
    }
    if result["pagination"] is not None:
        payload["pagination"] = result["pagination"]
    return payload


def list_products():
    category_id = request.args.get("category_id", type=int)
    raw_category = request.args.get("category_id")
    if raw_category not in (None, "") and category_id is None:
        raise ValidationError("El parámetro 'category_id' debe ser un entero.")
    result = product_service.list_products(
        search=request.args.get("search"),
        category_id=category_id,
        supplier_id=_optional_int("supplier_id", minimum=1),
        low_stock=_truthy("low_stock"),
        include_inactive=_truthy("include_inactive"),
        status=(request.args.get("status") or "").strip().lower() or None,
        approval_status=(request.args.get("approval_status") or "").strip().lower() or None,
        price_min=_optional_price("price_min"),
        price_max=_optional_price("price_max"),
        sale_price_currency=request.args.get("sale_price_currency") or None,
        stock_min=_optional_int("stock_min", minimum=0),
        stock_max=_optional_int("stock_max", minimum=0),
        updated_from=_optional_date("updated_from"),
        updated_to=_optional_date("updated_to", end_of_day=True),
        sort=request.args.get("sort"),
        page=_optional_int("page"),
        per_page=_optional_int("per_page"),
    )
    return jsonify(_items_payload(result))


def list_low_stock_products():
    result = product_service.list_products(low_stock=True)
    return jsonify(_items_payload(result))


def products_summary():
    return jsonify(product_service.products_summary())


def products_history_summary():
    return jsonify(product_history_service.history_summary(request.args))


def _export_number(key, value):
    if value is None or value == '':
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError(f"El filtro '{key}' debe ser numérico.")
    if not number.is_finite() or number < 0 or number > Decimal('999999999999'):
        raise ValidationError(f"El filtro '{key}' está fuera del rango permitido.")
    if key.startswith('price_'):
        return number
    if number != number.to_integral_value():
        raise ValidationError(f"El filtro '{key}' debe ser entero.")
    return int(number)


def _export_filters(raw):
    if not isinstance(raw, dict):
        raise ValidationError('Los filtros deben ser un objeto JSON.')
    numbers = {'supplier_id', 'category_id', 'price_min', 'price_max', 'stock_min', 'stock_max'}
    strings = {'search', 'status', 'sort', 'approval_status', 'sale_price_currency'}
    dates = {'updated_from', 'updated_to'}
    if set(raw) - (numbers | strings | dates | {'include_inactive'}):
        raise ValidationError('La exportación contiene filtros no permitidos; no admite paginación.')
    parsed = {}
    for key, value in raw.items():
        if key in numbers:
            parsed[key] = _export_number(key, value)
        elif key in strings:
            if value is not None and (not isinstance(value, str) or len(value) > 500):
                raise ValidationError(f"El filtro '{key}' debe ser un texto de hasta 500 caracteres.")
            parsed[key] = value or None
        elif key in dates:
            parsed[key] = _export_date(key, value)
        else:
            if type(value) is not bool:
                raise ValidationError('include_inactive debe ser booleano.')
            parsed[key] = value
    return parsed


def _export_date(key, value):
    if value is None or value == '':
        return None
    try:
        day = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValidationError(f"El filtro '{key}' debe tener formato YYYY-MM-DD.")
    return datetime.combine(day, time.max if key == 'updated_to' else time.min)


def export_products():
    body = _json_body()
    output, mime, filename = product_export_service.export_products(
        body.get('format'), body.get('options', {}), _export_filters(body.get('filters', {}))
    )
    return attachment_response(output, mime, filename)


def supplier_options():
    return jsonify(suppliers=product_supplier_service.active_suppliers())


def list_product_suppliers(product_id: int):
    return jsonify(
        items=product_supplier_service.list_for_product(product_id),
        suppliers=product_supplier_service.active_suppliers(),
    )


def create_product_supplier(product_id: int):
    link = product_supplier_service.create_link(product_id, _json_body())
    return jsonify(product_supplier_service.serialize(link)), 201


def update_product_supplier(product_id: int, link_id: int):
    link = product_supplier_service.update_link(product_id, link_id, _json_body())
    return jsonify(product_supplier_service.serialize(link))


def get_product(product_id: int):
    product = product_service.get_product_or_404(product_id)
    return jsonify(product_change_service.detail_payload(product))


def create_product():
    product = product_service.create_product(_json_body())
    return jsonify(product.to_dict()), 201


def approve_product(product_id: int):
    body = _json_body() if request.data else {}
    product = product_service.decide_product(product_id, approve=True, proposal_id=body.get("proposal_id"))
    return jsonify(product_change_service.detail_payload(product))


def reject_product(product_id: int):
    body = _json_body()
    product = product_service.decide_product(product_id, approve=False, reason=body.get("reason"), proposal_id=body.get("proposal_id"))
    return jsonify(product_change_service.detail_payload(product))


def update_product(product_id: int):
    product = product_service.update_product(product_id, _json_body())
    return jsonify(product_change_service.detail_payload(product))


def deactivate_product(product_id: int):
    product = product_service.deactivate_product(product_id)
    return jsonify(
        {
            "message": "Propuesta de desactivación enviada a revisión. El producto vigente se conserva.",
            "product": product_change_service.detail_payload(product),
        }
    )


def upload_product_image(product_id: int):
    """Sube/reemplaza la imagen (multipart/form-data, campo 'image')."""
    changes = None
    if "changes" in request.form:
        try:
            changes = json.loads(request.form["changes"])
        except (ValueError, TypeError):
            raise ValidationError("Los cambios deben contener un objeto JSON válido.")
        if not isinstance(changes, dict):
            raise ValidationError("Los cambios deben contener un objeto JSON válido.")
    product = product_image_service.set_image(product_id, request.files.get("image"), changes)
    return jsonify(
        {
            "message": "Imagen incorporada a la propuesta pendiente de revisión.",
            "image_url": product.image_url,
            "product": product_change_service.detail_payload(product),
        }
    )


def delete_product_image(product_id: int):
    product = product_image_service.remove_image(product_id)
    return jsonify(
        {
            "message": "Retiro de imagen incorporado a la propuesta. El producto vigente se conserva.",
            "product": product_change_service.detail_payload(product),
        }
    )
