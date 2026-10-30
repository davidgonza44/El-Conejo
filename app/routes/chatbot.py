"""Rutas JSON del chatbot interno de inventario."""
from flask import Blueprint

from app.controllers import chatbot_controller
from app.models.user import ROLE_SOPORTE_TI
from app.utils.auth_decorators import permission_required
from app.utils.permissions import PRODUCTS_READ, role_has_permission
from flask_login import current_user
from flask import jsonify
from functools import wraps


chatbot_bp = Blueprint("chatbot", __name__, url_prefix="/api/chatbot")


def _chat_access(view):
    guarded = permission_required(PRODUCTS_READ)(view)

    @wraps(view)
    def wrapper(*args, **kwargs):
        if getattr(current_user, "is_authenticated", False) and current_user.role == ROLE_SOPORTE_TI:
            return view(*args, **kwargs)
        if getattr(current_user, "is_authenticated", False) and not role_has_permission(current_user.role, PRODUCTS_READ):
            return jsonify({"error": "Acceso denegado."}), 403
        return guarded(*args, **kwargs)

    return wrapper


chatbot_bp.add_url_rule(
    "/message",
    view_func=_chat_access(chatbot_controller.post_message),
    methods=["POST"],
)
