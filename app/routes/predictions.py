"""API del módulo predictivo.

- Diagnóstico de suficiencia histórica (solo lectura).
- Exportación CSV de los resultados LIVE filtrados.
- Creación de una solicitud de reposición desde recomendaciones (confirmación humana).
  Confirmar la solicitud no crea la orden de compra.
"""
from flask import Blueprint

from app.controllers import predictions_controller, predictive_controller
from app.utils.auth_decorators import permission_required
from app.utils.permissions import PREDICTIONS_READ, REPLENISHMENT_CREATE

predictions_bp = Blueprint("predictions", __name__, url_prefix="/api/predictions")

_can_read = permission_required(PREDICTIONS_READ)
_can_buy = permission_required(REPLENISHMENT_CREATE)

predictions_bp.add_url_rule(
    "/status/<token>", endpoint="computation_status",
    view_func=_can_read(predictive_controller.computation_status), methods=["GET"],
)

predictions_bp.add_url_rule(
    "/export/<page>",
    endpoint="export",
    view_func=_can_read(predictive_controller.export),
    methods=["GET"],
)
predictions_bp.add_url_rule(
    "/replenishment/orders",
    endpoint="replenishment_order",
    view_func=_can_buy(predictive_controller.replenishment_order),
    methods=["POST"],
)

predictions_bp.add_url_rule(
    "/readiness",
    view_func=_can_read(predictions_controller.readiness_summary),
    methods=["GET"],
)
predictions_bp.add_url_rule(
    "/products",
    view_func=_can_read(predictions_controller.list_products),
    methods=["GET"],
)
predictions_bp.add_url_rule(
    "/products/<int:product_id>",
    view_func=_can_read(predictions_controller.get_product),
    methods=["GET"],
)

predictions_bp.add_url_rule(
    "/products/<int:product_id>/detail", endpoint="product_detail",
    view_func=_can_read(predictive_controller.product_detail), methods=["GET"],
)
