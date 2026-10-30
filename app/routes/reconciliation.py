"""Every page and API action checks persisted granular permissions."""
from flask import Blueprint
from app.controllers import reconciliation_controller as controller
from app.utils.auth_decorators import page_permission_required, permission_required

reconciliation_bp = Blueprint("reconciliation", __name__)

for path, endpoint, methods, permission in (
    ("/reconciliation", "page", ["GET"], "read"),
    ("/api/reconciliation", "listing", ["GET"], "read"),
    ("/api/reconciliation", "create", ["POST"], "create"),
    ("/api/reconciliation/cuts", "cuts", ["GET"], "read"),
    ("/api/reconciliation/cuts/simulate", "simulate", ["POST"], "create"),
    ("/api/reconciliation/cuts/<int:cut_id>/csv", "cut_csv", ["GET"], "read"),
    ("/api/reconciliation/cuts/<int:cut_id>/upload", "cut_upload", ["POST"], "create"),
    ("/api/reconciliation/cuts/<int:cut_id>", "cut_detail", ["GET"], "read"),
    ("/api/reconciliation/cuts/<int:cut_id>/retry", "retry_cut", ["POST"], "create"),
    ("/api/reconciliation/<int:run_id>", "detail", ["GET"], "read"),
    ("/api/reconciliation/<int:run_id>/confirm", "confirm", ["POST"], "create"),
    ("/api/reconciliation/<int:run_id>/discard", "discard", ["POST"], "create"),
    ("/api/reconciliation/<int:run_id>/lines/<int:line_id>/review", "review", ["POST"], "resolve"),
    ("/api/reconciliation/<int:run_id>/lines/<int:line_id>/resolve", "resolve", ["POST"], "resolve"),
):
    guard = page_permission_required if endpoint == "page" else permission_required
    reconciliation_bp.add_url_rule(path, endpoint=endpoint, methods=methods,
        view_func=guard("reconciliation:" + permission)(getattr(controller, endpoint)))
