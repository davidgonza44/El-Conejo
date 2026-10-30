"""Controlador de pedidos: traduce HTTP <-> servicio (sin efectos en stock)."""
from flask import jsonify, request

from app.controllers.sale_controller import _json_body
from app.services import order_service


def create_order():
    order = order_service.create_order(_json_body())
    payload = order.to_dict()
    payload["message"] = f"Pedido {order.order_number} guardado correctamente."
    return jsonify(payload), 201


def list_orders():
    return jsonify(order_service.list_orders(request.args.to_dict()))


def get_order(order_number: str):
    return jsonify(order_service.get_order(order_number).to_dict())


def update_order(order_number: str):
    return jsonify(order_service.update_order(order_number, _json_body()).to_dict())


def change_status(order_number: str):
    return jsonify(order_service.change_status(order_number, _json_body()).to_dict())


def change_payment_status(order_number: str):
    return jsonify(order_service.change_payment_status(order_number, _json_body()).to_dict())


def duplicate_order(order_number: str):
    order = order_service.duplicate_order(order_number)
    payload = order.to_dict()
    payload["message"] = f"Pedido {order.order_number} creado como copia de {order_number}."
    return jsonify(payload), 201


def order_options():
    return jsonify(order_service.order_options())
