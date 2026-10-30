"""Controlador de categorías: traduce HTTP <-> servicio."""
from flask import jsonify, request

from app.controllers import attachment_response
from app.services import category_export_service, category_service
from app.services.exceptions import ValidationError


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        raise ValidationError("El cuerpo de la petición debe ser un JSON válido.")
    return data


def _optional_int(name: str) -> int | None:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"El parámetro '{name}' debe ser un número entero.")


def list_categories():
    # Sin page/per_page conserva el contrato legado {count, items} con todas las categorías.
    result = category_service.list_categories(
        search=request.args.get("search"),
        status=(request.args.get("status") or "").strip().lower() or None,
        approval_status=(request.args.get("approval_status") or "").strip().lower() or None,
        sort=request.args.get("sort"),
        page=_optional_int("page"),
        per_page=_optional_int("per_page") if request.args.get("per_page") else _optional_int("page_size"),
    )
    payload = {"count": result["total"], "items": result["items"]}
    if result["pagination"] is not None:
        payload["pagination"] = result["pagination"]
    return jsonify(payload)


def export_categories():
    params = request.args.to_dict()
    output, mime, filename = category_export_service.export_categories(params.pop("format", None), params)
    return attachment_response(output, mime, filename)


def categories_summary():
    return jsonify(category_service.categories_summary())


def get_category(category_id: int):
    return jsonify(category_service.category_detail(category_id))


def create_category():
    category = category_service.create_category(_json_body())
    return jsonify(category.to_dict()), 201


def update_category(category_id: int):
    category = category_service.update_category(category_id, _json_body())
    return jsonify(category_service.category_detail(category.id))


def duplicate_category(category_id: int):
    category = category_service.duplicate_category(category_id)
    return jsonify(category.to_dict()), 201


def delete_category(category_id: int):
    category_service.delete_category(category_id)
    return jsonify({"message": f"Categoría {category_id} eliminada correctamente."})


def approve_category(category_id: int):
    data = _json_body() if request.is_json else {}
    category_service.decide_category(category_id, approve=True, proposal_id=data.get("proposal_id"))
    return jsonify(category_service.category_detail(category_id))


def reject_category(category_id: int):
    data = _json_body()
    category_service.decide_category(category_id, approve=False, reason=data.get("reason"), proposal_id=data.get("proposal_id"))
    return jsonify(category_service.category_detail(category_id))
