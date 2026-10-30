"""Rutas REST de productos.

Lectura: products:read. Propuestas: products:write, solo Asistente.
Decisiones: products:approve, solo Administrador. No hay edición directa.
"""
from flask import Blueprint

from app.controllers import product_controller
from app.utils.auth_decorators import permission_required
from app.utils.permissions import PRODUCTS_APPROVE, PRODUCTS_READ, PRODUCTS_WRITE, SUPPLIERS_READ

products_bp = Blueprint("products", __name__, url_prefix="/api")

_can_read = permission_required(PRODUCTS_READ)
_can_write = permission_required(PRODUCTS_WRITE)
_can_approve = permission_required(PRODUCTS_APPROVE)
_can_read_suppliers = permission_required(SUPPLIERS_READ)

products_bp.add_url_rule(
    "/products/export", view_func=_can_read(product_controller.export_products), methods=["POST"]
)

products_bp.add_url_rule(
    "/products", view_func=_can_read(product_controller.list_products), methods=["GET"]
)
products_bp.add_url_rule(
    "/products", view_func=_can_write(product_controller.create_product), methods=["POST"]
)
products_bp.add_url_rule(
    "/products/<int:product_id>/approve", view_func=_can_approve(product_controller.approve_product), methods=["POST"]
)
products_bp.add_url_rule(
    "/products/<int:product_id>/reject", view_func=_can_approve(product_controller.reject_product), methods=["POST"]
)
products_bp.add_url_rule(
    "/products/low-stock",
    view_func=_can_read(product_controller.list_low_stock_products),
    methods=["GET"],
)
products_bp.add_url_rule(
    "/products/summary",
    view_func=_can_read(product_controller.products_summary),
    methods=["GET"],
)
products_bp.add_url_rule(
    "/products/history/summary",
    view_func=_can_read(product_controller.products_history_summary),
    methods=["GET"],
)
products_bp.add_url_rule(
    "/products/supplier-options",
    view_func=_can_read(_can_read_suppliers(product_controller.supplier_options)),
    methods=["GET"],
)
products_bp.add_url_rule(
    "/products/<int:product_id>/suppliers",
    view_func=_can_read(_can_read_suppliers(product_controller.list_product_suppliers)),
    methods=["GET"],
)
products_bp.add_url_rule(
    "/products/<int:product_id>/suppliers",
    view_func=_can_write(product_controller.create_product_supplier),
    methods=["POST"],
)
products_bp.add_url_rule(
    "/products/<int:product_id>/suppliers/<int:link_id>",
    view_func=_can_write(product_controller.update_product_supplier),
    methods=["PATCH"],
)
products_bp.add_url_rule(
    "/products/<int:product_id>",
    view_func=_can_read(product_controller.get_product),
    methods=["GET"],
)
products_bp.add_url_rule(
    "/products/<int:product_id>",
    view_func=_can_write(product_controller.update_product),
    methods=["PUT", "PATCH"],
)
products_bp.add_url_rule(
    "/products/<int:product_id>",
    view_func=_can_write(product_controller.deactivate_product),
    methods=["DELETE"],
)
products_bp.add_url_rule(
    "/products/<int:product_id>/image",
    view_func=_can_write(product_controller.upload_product_image),
    methods=["POST"],
)
products_bp.add_url_rule(
    "/products/<int:product_id>/image",
    view_func=_can_write(product_controller.delete_product_image),
    methods=["DELETE"],
)
