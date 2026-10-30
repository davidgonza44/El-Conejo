"""Rutas REST de reportes (solo lectura).

Todos los endpoints requieren el permiso reports:read (admin e inventario).
El rol vendedor no tiene acceso a reportes administrativos.
"""
from flask import Blueprint

from app.controllers import report_controller
from app.utils.auth_decorators import permission_required
from app.utils.permissions import REPORTS_READ

reports_bp = Blueprint("reports", __name__, url_prefix="/api/reports")

_can_read = permission_required(REPORTS_READ)

_ENDPOINTS = {
    "/dashboard-summary": report_controller.dashboard_summary,
    "/stock-vs-minimum": report_controller.stock_vs_minimum,
    "/low-stock-products": report_controller.low_stock_products,
    "/products-without-movement": report_controller.products_without_movement,
    "/excess-stock-products": report_controller.excess_stock_products,
    "/entries-vs-exits": report_controller.entries_vs_exits,
    "/movements-by-category": report_controller.movements_by_category,
    "/top-products-by-exits": report_controller.top_products_by_exits,
    "/least-products-by-exits": report_controller.least_products_by_exits,
    "/inventory-adjustments": report_controller.inventory_adjustments,
    "/delivery-notes-by-period": report_controller.delivery_notes_by_period,
    "/top-delivered-products": report_controller.top_delivered_products,
    "/delivery-notes-by-user": report_controller.delivery_notes_by_user,
    "/delivery-notes-by-customer": report_controller.delivery_notes_by_customer,
    "/sales/export": report_controller.export_sales_report,
}

for rule, view in _ENDPOINTS.items():
    reports_bp.add_url_rule(rule, view_func=_can_read(view), methods=["GET"])
