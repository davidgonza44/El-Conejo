"""Rutas REST de pedidos: mismos permisos del módulo Ventas (sales:read / sales:write)."""
from flask import Blueprint

from app.controllers import order_controller
from app.utils.auth_decorators import permission_required
from app.utils.permissions import SALES_READ, SALES_WRITE

orders_bp = Blueprint("orders", __name__, url_prefix="/api/orders")

_can_read = permission_required(SALES_READ)
_can_write = permission_required(SALES_WRITE)

orders_bp.add_url_rule("", view_func=_can_read(order_controller.list_orders), methods=["GET"])
orders_bp.add_url_rule("", view_func=_can_write(order_controller.create_order), methods=["POST"])
orders_bp.add_url_rule("/options", view_func=_can_read(order_controller.order_options), methods=["GET"])
orders_bp.add_url_rule("/<order_number>", view_func=_can_read(order_controller.get_order), methods=["GET"])
orders_bp.add_url_rule("/<order_number>", view_func=_can_write(order_controller.update_order), methods=["PATCH"])
orders_bp.add_url_rule("/<order_number>/status", view_func=_can_write(order_controller.change_status), methods=["PATCH"])
orders_bp.add_url_rule("/<order_number>/payment-status", view_func=_can_write(order_controller.change_payment_status), methods=["PATCH"])
orders_bp.add_url_rule("/<order_number>/duplicate", view_func=_can_write(order_controller.duplicate_order), methods=["POST"])
