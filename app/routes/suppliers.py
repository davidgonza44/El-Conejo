"""Supplier access is independent of physical purchase reception permissions."""
from flask import Blueprint
from app.controllers import supplier_controller as controller
from app.utils.auth_decorators import permission_required

suppliers_bp = Blueprint("suppliers", __name__)

for path, endpoint, methods, permission in (
    ("/api/suppliers", "listing", ["GET"], "suppliers:read"),
    ("/api/suppliers", "create", ["POST"], "suppliers:propose"),
    ("/api/suppliers/export", "export", ["GET"], "suppliers:read"),
    ("/api/suppliers/<int:supplier_id>", "detail", ["GET"], "suppliers:read"),
    ("/api/suppliers/<int:supplier_id>", "edit", ["PATCH"], "suppliers:propose"),
    ("/api/suppliers/<int:supplier_id>", "delete", ["DELETE"], "suppliers:approve"),
    ("/api/suppliers/<int:supplier_id>/preferred", "preferred", ["POST"], "suppliers:approve"),
    ("/api/suppliers/<int:supplier_id>/status-proposals", "propose_status", ["POST"], "suppliers:propose"),
    ("/api/suppliers/<int:supplier_id>/status", "status", ["POST"], "suppliers:approve"),
    ("/api/suppliers/<int:supplier_id>/approve", "approve", ["POST"], "suppliers:approve"),
    ("/api/suppliers/<int:supplier_id>/reject", "reject", ["POST"], "suppliers:approve"),
):
    suppliers_bp.add_url_rule(path, endpoint, permission_required(permission)(getattr(controller, endpoint)), methods=methods)
