"""All purchase actions are authorized against dynamic persisted RBAC."""
from flask import Blueprint
from app.controllers import purchase_controller as controller
from app.controllers import replenishment_request_controller as requests
from app.utils.auth_decorators import page_permission_required, permission_required

purchases_bp = Blueprint("purchases", __name__)

_HTML_PAGES = {"page", "receptions"}

for path, endpoint, methods, permission in (
    ("/purchases", "page", ["GET"], "read"),
    ("/purchases/receptions", "receptions", ["GET"], "read"),
    ("/purchases/<int:order_id>/print", "print_order", ["GET"], "read"),
    ("/api/purchases", "listing", ["GET"], "read"),
    ("/api/purchases", "create", ["POST"], "write"),
    ("/api/purchases/options", "options", ["GET"], "read"),
    ("/api/purchases/export.csv", "export", ["GET"], "read"),
    ("/api/purchases/suppliers", "supplier_create", ["POST"], "write"),
    ("/api/purchases/<int:order_id>", "detail", ["GET"], "read"),
    ("/api/purchases/<int:order_id>", "edit", ["PATCH"], "write"),
    ("/api/purchases/<int:order_id>/duplicate", "duplicate", ["POST"], "write"),
    ("/api/purchases/<int:order_id>/approve", "approve", ["POST"], "approve"),
    ("/api/purchases/<int:order_id>/reject", "reject", ["POST"], "approve"),
    ("/api/purchases/<int:order_id>/cancel", "cancel", ["POST"], "cancel"),
    ("/api/purchases/<int:order_id>/closure-requests", "request_closure", ["POST"], "close_request"),
    ("/api/purchases/<int:order_id>/closure-requests/approve", "approve_closure", ["POST"], "close_approve"),
    ("/api/purchases/<int:order_id>/closure-requests/reject", "reject_closure", ["POST"], "close_approve"),
    ("/api/purchases/<int:order_id>/receptions", "receive", ["POST"], "receive"),
):
    guard = page_permission_required if endpoint in _HTML_PAGES else permission_required
    purchases_bp.add_url_rule(path, endpoint=endpoint, view_func=guard("purchases:" + permission)(getattr(controller, endpoint)), methods=methods)

purchases_bp.add_url_rule(
    "/purchases/requests", endpoint="requests",
    view_func=page_permission_required("replenishment:read")(requests.page), methods=["GET"])
purchases_bp.add_url_rule(
    "/purchases/requests/<int:request_id>", endpoint="request_detail",
    view_func=page_permission_required("replenishment:read")(requests.detail_page), methods=["GET"])

from app.controllers import reception_controller

for path, endpoint in (
    ('/api/purchases/receptions', 'listing'),
    ('/api/purchases/receptions/options', 'options'),
    ('/api/purchases/receptions/orders/<int:order_id>', 'order'),
    ('/api/purchases/receptions/export', 'export'),
    ('/api/purchases/receptions/<int:receipt_id>', 'detail'),
    ('/purchases/receptions/<int:receipt_id>/print', 'print_receipt'),
    ('/purchases/receptions/<int:receipt_id>/pdf', 'pdf'),
):
    purchases_bp.add_url_rule(path, endpoint='reception_' + endpoint,
        view_func=permission_required('purchases:read')(getattr(reception_controller, endpoint)), methods=['GET'])
