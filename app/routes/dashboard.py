"""Ruta REST de Inicio (solo lectura). Misma política que la página: admin e inventario."""
from flask import Blueprint

from app.controllers import dashboard_controller
from app.models.user import ROLE_ADMIN, ROLE_ASISTENTE, ROLE_INVENTARIO
from app.utils.auth_decorators import role_required

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/api/dashboard")

dashboard_bp.add_url_rule(
    "/summary",
    view_func=role_required(ROLE_ADMIN, ROLE_INVENTARIO, ROLE_ASISTENTE)(dashboard_controller.summary),
    methods=["GET"],
)
