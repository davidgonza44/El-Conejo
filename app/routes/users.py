"""Rutas REST de gestión de usuarios (permiso users:manage; el rol admin lo tiene siempre)."""
from flask import Blueprint

from app.controllers import user_controller
from app.utils.auth_decorators import permission_required
from app.utils.csrf_guard import csrf_protected
from app.utils.permissions import USERS_MANAGE

users_bp = Blueprint("users", __name__, url_prefix="/api/users")

_admin_only = permission_required(USERS_MANAGE)

users_bp.add_url_rule(
    "", view_func=_admin_only(user_controller.list_users), methods=["GET"]
)
users_bp.add_url_rule(
    "", view_func=_admin_only(csrf_protected(user_controller.create_user)), methods=["POST"]
)
users_bp.add_url_rule(
    "/<int:user_id>", view_func=_admin_only(user_controller.get_user), methods=["GET"]
)
users_bp.add_url_rule(
    "/<int:user_id>",
    view_func=_admin_only(csrf_protected(user_controller.update_user)),
    methods=["PUT"],
)
