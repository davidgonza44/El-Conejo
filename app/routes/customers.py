"""Rutas REST del directorio de clientes.

Lectura: customers:read (admin, inventario, vendedor).
Escritura: customers:write (admin, vendedor).
"""
from flask import Blueprint

from app.controllers import customer_controller
from app.utils.auth_decorators import permission_required
from app.utils.permissions import CUSTOMERS_READ, CUSTOMERS_WRITE

customers_bp = Blueprint("customers", __name__, url_prefix="/api/customers")

_can_read = permission_required(CUSTOMERS_READ)
_can_write = permission_required(CUSTOMERS_WRITE)

customers_bp.add_url_rule("", view_func=_can_read(customer_controller.list_customers), methods=["GET"])
customers_bp.add_url_rule("", view_func=_can_write(customer_controller.create_customer), methods=["POST"])
customers_bp.add_url_rule("/<int:customer_id>", view_func=_can_read(customer_controller.get_customer), methods=["GET"])
customers_bp.add_url_rule("/<int:customer_id>", view_func=_can_write(customer_controller.update_customer), methods=["PUT"])
customers_bp.add_url_rule("/<int:customer_id>", view_func=_can_write(customer_controller.delete_customer), methods=["DELETE"])
