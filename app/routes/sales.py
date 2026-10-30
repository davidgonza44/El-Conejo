"""Rutas REST de ventas.

Lectura: sales:read (admin, inventario, vendedor).
Registro: sales:create (vendedor); la salida de stock la hace inventory_service.
"""
from flask import Blueprint

from app.controllers import dashboard_controller, sale_controller
from app.utils.auth_decorators import permission_required
from app.utils.permissions import SALES_READ, SALES_CREATE

sales_bp = Blueprint("sales", __name__, url_prefix="/api/sales")

_can_read = permission_required(SALES_READ)
_can_write = permission_required(SALES_CREATE)

sales_bp.add_url_rule("", view_func=_can_read(sale_controller.list_sales), methods=["GET"])
sales_bp.add_url_rule("", view_func=_can_write(sale_controller.create_sale), methods=["POST"])
sales_bp.add_url_rule("/dashboard", view_func=_can_read(dashboard_controller.sales_dashboard), methods=["GET"])
sales_bp.add_url_rule("/products", view_func=_can_read(sale_controller.sale_products), methods=["GET"])
sales_bp.add_url_rule("/options", view_func=_can_read(sale_controller.sale_options), methods=["GET"])
sales_bp.add_url_rule("/<sale_number>", view_func=_can_read(sale_controller.get_sale), methods=["GET"])
