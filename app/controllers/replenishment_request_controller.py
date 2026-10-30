"""Bandeja de solicitudes de reposición. No lista órdenes de compra."""
from datetime import datetime
from urllib.parse import urlencode

from flask import jsonify, render_template, request
from flask_login import current_user

from app.controllers.purchase_controller import _mutate as purchase_mutate, reference
from app.controllers.sale_controller import _json_body
from app.services import replenishment_request_service as service
from app.services.inventory_service import COMMERCIAL_ZONE
from app.services.exceptions import ValidationError
from app.services.purchase_query_service import page_values
from app.models.replenishment_request import STATUSES, STATUS_LABELS
from app.models.purchase import STATUS_LABELS as ORDER_STATUS_LABELS
from app.models.user import ROLE_INVENTARIO
from app.utils.permissions import role_has_permission


def _can_process():
    return role_has_permission(current_user.role, "replenishment:process")


def _shell():
    return {
        "reference_mode": reference(), "pro_shell": True, "can_process": _can_process(),
        "today": datetime.now(COMMERCIAL_ZONE).date().isoformat(),
    }


def _mutate(fn, *args, status=200):
    return purchase_mutate(fn, *args, status=status)


def page():
    status_code = 200
    try:
        view = service.list_requests(request.args)
    except ValidationError as error:
        status_code = 400
        view = {
            "items": [], "filter_error": error.message,
            "filters": {key: str(request.args.get(key) or "").strip()[:150]
                        for key in ("search", "status", "start", "end")},
            "pagination": page_values({}, 0),
            "statuses": [{"value": key, "label": STATUS_LABELS[key]} for key in STATUSES],
        }
    params = {**view["filters"], "per_page": view["pagination"]["per_page"]}
    if reference():
        params["ref"] = "1"
    view["query"] = urlencode(params)
    return render_template(
        "purchases/requests.html", view=view,
        show_creator=current_user.role != ROLE_INVENTARIO, **_shell()), status_code


def listing():
    return jsonify(service.list_requests(request.args))


def detail_page(request_id):
    row = service.get_visible(request_id)
    quotes = {"covering": [], "partial": [], "notice": ""}
    suppliers = []
    if _can_process() and row.status == "processing":
        quotes = service.quote_options(row)
        suppliers = service.approved_suppliers(row)
    payload = row.to_dict()
    order = payload["purchase_order"]
    return render_template(
        "purchases/request_detail.html", row=payload, quotes=quotes, suppliers=suppliers,
        no_supplier_notice=service.NO_ASSOCIATED_SUPPLIER,
        order_status_label=ORDER_STATUS_LABELS.get(order["status"], "Estado no registrado") if order else "",
        **_shell())


def detail(request_id):
    return jsonify(service.get_visible(request_id).to_dict())


def start(request_id):
    return _mutate(service.start, request_id)


def prepare(request_id):
    return _mutate(service.prepare_order, request_id, _json_body())


def create_quote(request_id):
    return _mutate(service.create_quote, request_id, _json_body(), status=201)


def cancel(request_id):
    return _mutate(service.cancel, request_id)


def create_from_shortage(order_id):
    return _mutate(service.create_from_shortage, order_id, _json_body(), status=201)
