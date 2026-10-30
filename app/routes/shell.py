"""Rutas REST de la cáscara compartida (Ferretería Pro): búsqueda global y campana.

La búsqueda filtra sus grupos por los permisos del rol; /alerts (stock) requiere products:read y se
conserva; /notifications es la bandeja persistente propia (marcar leída / todas leídas).
"""
from flask import Blueprint

from app.controllers import shell_controller
from app.utils.auth_decorators import login_required, permission_required
from app.utils.permissions import PRODUCTS_READ

shell_bp = Blueprint("shell", __name__, url_prefix="/api/shell")

shell_bp.add_url_rule("/search", view_func=login_required(shell_controller.search), methods=["GET"])
shell_bp.add_url_rule("/alerts", view_func=permission_required(PRODUCTS_READ)(shell_controller.alerts), methods=["GET"])
# Notificaciones persistentes propias (bandeja de la campana): cualquier usuario autenticado.
shell_bp.add_url_rule("/notifications", view_func=login_required(shell_controller.notifications), methods=["GET"])
shell_bp.add_url_rule("/notifications/read-all", view_func=login_required(shell_controller.mark_all_notifications_read),
                      methods=["POST"])
shell_bp.add_url_rule("/notifications/<int:notification_id>/read",
                      view_func=login_required(shell_controller.mark_notification_read), methods=["POST"])
