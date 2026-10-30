"""Rutas REST de categorías.

Lectura: products:read. Propuestas: categories:propose. Decisiones: categories:approve.
Modificación: categories:propose; nunca escritura directa sobre un registro aprobado.
La autorización de estos endpoints no depende del menú.
"""
from flask import Blueprint

from app.controllers import category_controller
from app.utils.auth_decorators import permission_required
from app.utils.permissions import CATEGORIES_APPROVE, CATEGORIES_PROPOSE, PRODUCTS_READ

categories_bp = Blueprint("categories", __name__, url_prefix="/api")

_can_read = permission_required(PRODUCTS_READ)
_can_propose = permission_required(CATEGORIES_PROPOSE)
_can_approve = permission_required(CATEGORIES_APPROVE)

categories_bp.add_url_rule(
    "/categories",
    view_func=_can_read(category_controller.list_categories),
    methods=["GET"],
)
categories_bp.add_url_rule(
    "/categories",
    view_func=_can_propose(category_controller.create_category),
    methods=["POST"],
)
categories_bp.add_url_rule(
    "/categories/export",
    view_func=_can_read(category_controller.export_categories),
    methods=["GET"],
)
categories_bp.add_url_rule(
    "/categories/summary",
    view_func=_can_read(category_controller.categories_summary),
    methods=["GET"],
)
categories_bp.add_url_rule(
    "/categories/<int:category_id>",
    view_func=_can_read(category_controller.get_category),
    methods=["GET"],
)
categories_bp.add_url_rule(
    "/categories/<int:category_id>/duplicate",
    view_func=_can_propose(category_controller.duplicate_category),
    methods=["POST"],
)
categories_bp.add_url_rule(
    "/categories/<int:category_id>",
    view_func=_can_propose(category_controller.update_category),
    methods=["PUT", "PATCH"],
)
categories_bp.add_url_rule(
    "/categories/<int:category_id>",
    view_func=_can_propose(category_controller.delete_category),
    methods=["DELETE"],
)
categories_bp.add_url_rule("/categories/<int:category_id>/approve",
                          view_func=_can_approve(category_controller.approve_category), methods=["POST"])
categories_bp.add_url_rule("/categories/<int:category_id>/reject",
                          view_func=_can_approve(category_controller.reject_category), methods=["POST"])
