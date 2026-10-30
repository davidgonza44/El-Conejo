"""Rutas REST de cotizaciones: mismos permisos del módulo Ventas (sales:read / sales:write)."""
from flask import Blueprint

from app.controllers import quote_controller
from app.utils.auth_decorators import permission_required
from app.utils.permissions import SALES_READ, SALES_WRITE

quotes_bp = Blueprint("quotes", __name__, url_prefix="/api/quotes")

_can_read = permission_required(SALES_READ)
_can_write = permission_required(SALES_WRITE)

quotes_bp.add_url_rule("", view_func=_can_read(quote_controller.list_quotes), methods=["GET"])
quotes_bp.add_url_rule("", view_func=_can_write(quote_controller.create_quote), methods=["POST"])
quotes_bp.add_url_rule("/options", view_func=_can_read(quote_controller.quote_options), methods=["GET"])
quotes_bp.add_url_rule("/<quote_number>", view_func=_can_read(quote_controller.get_quote), methods=["GET"])
quotes_bp.add_url_rule("/<quote_number>/status", view_func=_can_write(quote_controller.change_status), methods=["PATCH"])
