"""Controlador de clientes: traduce HTTP <-> servicio."""
from flask import jsonify, request

from app.services import customer_service
from app.services.exceptions import ValidationError


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        raise ValidationError("El cuerpo de la petición debe ser un JSON válido.")
    return data


def list_customers():
    return jsonify(customer_service.list_customers(request.args.to_dict()))


def get_customer(customer_id: int):
    return jsonify(customer_service.get_customer(customer_id).to_dict())


def create_customer():
    customer = customer_service.create_customer(_json_body())
    return jsonify(customer.to_dict()), 201


def update_customer(customer_id: int):
    return jsonify(customer_service.update_customer(customer_id, _json_body()).to_dict())


def delete_customer(customer_id: int):
    return jsonify(customer_service.delete_customer(customer_id))
