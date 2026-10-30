"""Rutas REST de notas de entrega.

Crear: admin y vendedor. Leer: admin, inventario y vendedor.
Cancelar: admin e inventario.
"""
from flask import Blueprint

from app.controllers import delivery_note_controller
from app.utils.auth_decorators import permission_required
from app.utils.permissions import (
    DELIVERY_NOTES_CANCEL,
    DELIVERY_NOTES_CREATE,
    DELIVERY_NOTES_READ,
)

delivery_notes_bp = Blueprint("delivery_notes", __name__, url_prefix="/api/delivery-notes")

delivery_notes_bp.add_url_rule(
    "",
    view_func=permission_required(DELIVERY_NOTES_CREATE)(
        delivery_note_controller.create_note
    ),
    methods=["POST"],
)
delivery_notes_bp.add_url_rule(
    "",
    view_func=permission_required(DELIVERY_NOTES_READ)(
        delivery_note_controller.list_notes
    ),
    methods=["GET"],
)
delivery_notes_bp.add_url_rule(
    "/<int:note_id>",
    view_func=permission_required(DELIVERY_NOTES_READ)(
        delivery_note_controller.get_note
    ),
    methods=["GET"],
)
delivery_notes_bp.add_url_rule(
    "/<int:note_id>/cancel",
    view_func=permission_required(DELIVERY_NOTES_CANCEL)(
        delivery_note_controller.cancel_note
    ),
    methods=["POST"],
)
delivery_notes_bp.add_url_rule(
    "/<int:note_id>/pdf",
    view_func=permission_required(DELIVERY_NOTES_READ)(
        delivery_note_controller.download_pdf
    ),
    methods=["GET"],
)
