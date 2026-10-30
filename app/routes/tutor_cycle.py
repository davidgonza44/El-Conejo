"""Solicitud de reposición y cotización de proveedor."""
from flask import Blueprint

from app.controllers import replenishment_request_controller as requests
from app.controllers import tutor_cycle_controller as controller
from app.utils.auth_decorators import permission_required

cycle_bp = Blueprint("tutor_cycle", __name__)

cycle_bp.add_url_rule(
    "/api/replenishment-requests", endpoint="create_request",
    view_func=permission_required("replenishment:create")(controller.create_request), methods=["POST"],
)
cycle_bp.add_url_rule(
    "/api/replenishment-requests", endpoint="list_requests",
    view_func=permission_required("replenishment:read")(requests.listing), methods=["GET"],
)
cycle_bp.add_url_rule(
    "/api/replenishment-requests/<int:request_id>", endpoint="detail_request",
    view_func=permission_required("replenishment:read")(requests.detail), methods=["GET"],
)
cycle_bp.add_url_rule(
    "/api/replenishment-requests/<int:request_id>/start", endpoint="start_request",
    view_func=permission_required("replenishment:process")(requests.start), methods=["POST"],
)
cycle_bp.add_url_rule(
    "/api/replenishment-requests/<int:request_id>/quotes", endpoint="create_request_quote",
    view_func=permission_required("replenishment:process")(requests.create_quote), methods=["POST"],
)
cycle_bp.add_url_rule(
    "/api/replenishment-requests/<int:request_id>/prepare", endpoint="prepare_request",
    view_func=permission_required("replenishment:process")(requests.prepare), methods=["POST"],
)
cycle_bp.add_url_rule(
    "/api/replenishment-requests/<int:request_id>/cancel", endpoint="cancel_request",
    view_func=permission_required("replenishment:process")(requests.cancel), methods=["POST"],
)
cycle_bp.add_url_rule(
    "/api/replenishment-requests/from-order/<int:order_id>", endpoint="create_request_from_shortage",
    view_func=permission_required("replenishment:create")(requests.create_from_shortage), methods=["POST"],
)
cycle_bp.add_url_rule(
    "/api/supplier-quotes", endpoint="create_quote",
    view_func=permission_required("supplier_quotes:write")(controller.create_quote), methods=["POST"],
)
cycle_bp.add_url_rule(
    "/api/supplier-quotes/<int:quote_id>/cancel", endpoint="cancel_quote",
    view_func=permission_required("supplier_quotes:write")(controller.cancel_quote), methods=["POST"],
)
