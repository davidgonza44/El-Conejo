"""Opt-in read-only report routes, separate from the production predictive selector."""
from flask import Blueprint

from app.controllers import experimental_2025_controller as controller
from app.utils.auth_decorators import permission_required
from app.utils.permissions import PREDICTIONS_READ


experimental_2025_bp = Blueprint("experimental_2025", __name__)
_can_read = permission_required(PREDICTIONS_READ)
experimental_2025_bp.add_url_rule(
    "/experimental/2025", "page", _can_read(controller.page), methods=["GET"],
)
experimental_2025_bp.add_url_rule(
    "/api/experimental/2025/readiness", "readiness", _can_read(controller.readiness), methods=["GET"],
)


def register_experimental_2025(app):
    """Explicit registration hook; never reads CSVs, initializes DB or creates orders."""
    if "experimental_2025" not in app.blueprints:
        app.register_blueprint(experimental_2025_bp)
