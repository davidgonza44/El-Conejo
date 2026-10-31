"""Controlador de movimientos de inventario: traduce HTTP <-> servicio."""
from flask import jsonify, request

from app.controllers import attachment_response
from app.services import inventory_adjustment_service, inventory_service, movement_export_service, product_service
from app.services.exceptions import ValidationError


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        raise ValidationError("El cuerpo de la petición debe ser un JSON válido.")
    return data


def _movement_response(movement, status_code: int = 201):
    """Devuelve el movimiento creado junto con el producto actualizado."""
    return (
        jsonify(
            {
                "movement": inventory_service.movement_data(movement),
                "product": movement.product.to_dict(),
            }
        ),
        status_code,
    )


def register_entry():
    movement, created = inventory_service.register_manual("entrada", _json_body())
    return _movement_response(movement, 201 if created else 200)


def register_exit():
    movement, created = inventory_service.register_manual("salida", _json_body())
    return _movement_response(movement, 201 if created else 200)


def register_adjustment():
    row = inventory_adjustment_service.create_request(_json_body())
    return jsonify(row.to_dict()), 201


def list_adjustments():
    return jsonify(inventory_adjustment_service.list_requests(request.args.to_dict()))


def adjustment_detail(request_id):
    return jsonify(inventory_adjustment_service.get_request(request_id).to_dict())


def approve_adjustment(request_id):
    return jsonify(inventory_adjustment_service.review_request(request_id, _json_body(), approve=True).to_dict())


def reject_adjustment(request_id):
    return jsonify(inventory_adjustment_service.review_request(request_id, _json_body(), approve=False).to_dict())


def list_movements():
    return jsonify(inventory_service.query_movements(request.args.to_dict()))


def export_movements():
    params = request.args.to_dict()
    output, mime, filename = movement_export_service.export_movements(params.pop("format", None), params)
    return attachment_response(output, mime, filename)


def movement_detail(movement_id):
    return jsonify(inventory_service.get_movement(movement_id))


def movement_summary():
    return jsonify(inventory_service.movement_summary(request.args.to_dict()))


def movement_users():
    return jsonify({"items": inventory_service.movement_users()})


def list_product_movements(product_id: int):
    movements = inventory_service.list_movements(
        movement_type=request.args.get("movement_type"),
        product_id=product_id,
        limit=request.args.get("limit", type=int),
    )
    presentations = inventory_service.movement_presentations(movements)
    return jsonify({"count": len(movements), "items": [
        {**m.to_dict(), **presentations[m.id]} for m in movements
    ]})


def list_low_stock_products():
    result = product_service.list_products(low_stock=True)
    return jsonify(
        {
            "count": result["total"],
            "items": [item.to_dict() for item in result["items"]],
        }
    )
