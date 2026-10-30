"""Rutas REST de movimientos de inventario.

Entradas, salidas y ajustes: roles con inventory:move (admin, inventario).
Historial de movimientos: roles con inventory:read (admin, inventario).
Bajo stock: roles con products:read (todos los roles).
"""
from flask import Blueprint

from app.controllers import dashboard_controller, inventory_controller
from app.utils.auth_decorators import login_required, permission_required
from app.utils.permissions import (
    INVENTORY_MOVE, INVENTORY_READ, PRODUCTS_READ,
    INVENTORY_ADJUSTMENTS_CREATE, INVENTORY_ADJUSTMENTS_APPROVE,
)

inventory_bp = Blueprint("inventory", __name__, url_prefix="/api/inventory")

_can_move = permission_required(INVENTORY_MOVE)
_can_read = permission_required(INVENTORY_READ)

inventory_bp.add_url_rule("/summary-dashboard", view_func=_can_read(dashboard_controller.inventory_summary), methods=["GET"])
inventory_bp.add_url_rule("/movements/export", view_func=_can_read(inventory_controller.export_movements), methods=["GET"])
inventory_bp.add_url_rule("/movements/<int:movement_id>", view_func=_can_read(inventory_controller.movement_detail), methods=["GET"])
inventory_bp.add_url_rule("/movements/summary", view_func=_can_read(inventory_controller.movement_summary), methods=["GET"])
inventory_bp.add_url_rule("/movements/users", view_func=_can_read(inventory_controller.movement_users), methods=["GET"])

inventory_bp.add_url_rule(
    "/entry", view_func=_can_move(inventory_controller.register_entry), methods=["POST"]
)
inventory_bp.add_url_rule(
    "/exit", view_func=_can_move(inventory_controller.register_exit), methods=["POST"]
)
inventory_bp.add_url_rule(
    "/adjustment",
    view_func=permission_required(INVENTORY_ADJUSTMENTS_CREATE)(inventory_controller.register_adjustment),
    methods=["POST"],
)
inventory_bp.add_url_rule("/adjustments", view_func=login_required(inventory_controller.list_adjustments), methods=["GET"])
inventory_bp.add_url_rule("/adjustments/<int:request_id>", view_func=login_required(inventory_controller.adjustment_detail), methods=["GET"])
inventory_bp.add_url_rule("/adjustments/<int:request_id>/approve", view_func=permission_required(INVENTORY_ADJUSTMENTS_APPROVE)(inventory_controller.approve_adjustment), methods=["POST"])
inventory_bp.add_url_rule("/adjustments/<int:request_id>/reject", view_func=permission_required(INVENTORY_ADJUSTMENTS_APPROVE)(inventory_controller.reject_adjustment), methods=["POST"])
inventory_bp.add_url_rule(
    "/movements",
    view_func=_can_read(inventory_controller.list_movements),
    methods=["GET"],
)
inventory_bp.add_url_rule(
    "/products/<int:product_id>/movements",
    view_func=_can_read(inventory_controller.list_product_movements),
    methods=["GET"],
)
inventory_bp.add_url_rule(
    "/low-stock",
    view_func=permission_required(PRODUCTS_READ)(
        inventory_controller.list_low_stock_products
    ),
    methods=["GET"],
)
