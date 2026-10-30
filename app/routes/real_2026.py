"""Historical 2026 routes: isolated analytical data, existing permissions."""
from functools import wraps

from flask import Blueprint, jsonify, request
from flask_wtf.csrf import validate_csrf
from wtforms.validators import ValidationError as CsrfError

from app.controllers import real_2026_controller as controller
from app.utils.auth_decorators import page_permission_required, permission_required


real_2026_bp = Blueprint("real_2026", __name__)


def csrf_required(view):
    @wraps(view)
    def protected(*args, **kwargs):
        try:
            validate_csrf(request.headers.get("X-CSRFToken"))
        except CsrfError:
            return jsonify(error="Token CSRF inválido o ausente."), 400
        return view(*args, **kwargs)
    return protected


real_2026_bp.add_url_rule(
    "/historical-2026", "page",
    page_permission_required("historical_imports:read")(controller.page), methods=["GET"],
)

for path, endpoint, permissions, write in (
    ("", "listing", ("historical_imports:read",), False),
    ("/preview", "preview", ("historical_imports:upload", "historical_imports:review"), True),
    ("/<string:public_id>", "detail", ("historical_imports:read",), False),
    ("/<string:public_id>/confirm", "confirm", ("historical_imports:confirm",), True),
    ("/<string:public_id>/revert", "revert", ("historical_imports:revert",), True),
    ("/<string:public_id>/products", "products", ("historical_imports:read",), False),
    ("/<string:public_id>/products/<int:product_id>", "history", ("historical_imports:read",), False),
    ("/<string:public_id>/products/<int:product_id>/analysis", "analysis",
     ("historical_imports:read", "predictions:read"), False),
):
    view = getattr(controller, endpoint)
    if write:
        view = csrf_required(view)
    for permission in reversed(permissions):
        view = permission_required(permission)(view)
    real_2026_bp.add_url_rule("/api/historical-2026/imports" + path, endpoint,
                             view, methods=["POST" if write else "GET"])


@real_2026_bp.errorhandler(413)
def too_large(_error):
    return jsonify(error="El paquete supera el límite de 64 MiB."), 413


@real_2026_bp.errorhandler(500)
def unavailable(_error):
    return jsonify(error="No se pudo completar la operación del histórico real 2026."), 500
