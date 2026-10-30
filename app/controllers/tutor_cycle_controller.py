"""HTTP de solicitud de reposición y cotización de proveedor."""
from app.controllers.purchase_controller import _mutate as purchase_mutate
from app.controllers.replenishment_request_controller import _mutate as request_mutate
from app.controllers.sale_controller import _json_body
from app.services import replenishment_request_service, supplier_quote_service


def _mutate(fn, *args, status=200):
    return purchase_mutate(fn, *args, status=status)


def create_request():
    return request_mutate(replenishment_request_service.create, _json_body(), status=201)


def create_quote():
    return _mutate(supplier_quote_service.create, _json_body(), status=201)


def cancel_quote(quote_id):
    return _mutate(supplier_quote_service.cancel, quote_id)
