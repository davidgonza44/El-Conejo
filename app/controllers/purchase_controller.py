"""Purchase HTTP contracts and common live/reference presentation."""
from datetime import datetime
from urllib.parse import urlencode, urlsplit

from flask import abort, jsonify, render_template, request, send_file
from flask_login import current_user
from flask_wtf.csrf import ValidationError as CsrfValidationError, validate_csrf
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.controllers.sale_controller import _json_body
from app.models.purchase import Supplier
from app.services import purchase_service as service, purchase_query_service as queries
from app.services.exceptions import ValidationError
from app.services.export_files import csv_file
from app.utils import purchase_reference
from app.utils.permissions import role_has_permission
from app.utils.purchase_currency import format_amount


def reference():
    return request.args.get("ref") == "1"


def _mutation_guard():
    # Defend against accidental live API submission from the preview, even with query stripped.
    from urllib.parse import parse_qs
    referer = request.headers.get("Referer", "")
    if reference() or parse_qs(urlsplit(referer).query).get("ref") == ["1"]:
        raise ValidationError("La referencia es una vista previa: no admite escrituras.", status_code=409)
    origin = request.headers.get("Origin")
    if origin and origin != request.host_url.rstrip("/"):
        raise ValidationError("Origen de solicitud no permitido.", status_code=403)
    if not request.is_json:
        raise ValidationError("Las operaciones de compras requieren JSON.", status_code=415)
    if not queries.available():
        raise ValidationError("Compras no está disponible. Falta aplicar la migración de Compras.", status_code=503)


def _context(view):
    return {"view": view, "reference_mode": reference(), "pro_shell": True, "movements_live": not reference(),
            "can_write": role_has_permission(current_user.role, "purchases:write"),
            "can_receive": role_has_permission(current_user.role, "purchases:receive"),
            "can_approve": role_has_permission(current_user.role, "purchases:approve"),
            "can_cancel": role_has_permission(current_user.role, "purchases:cancel"),
            "can_view_cost": role_has_permission(current_user.role, "products:view_cost"),
            "can_close_request": role_has_permission(current_user.role, "purchases:close_request"),
            "can_close_approve": role_has_permission(current_user.role, "purchases:close_approve"),
            "can_request_shortage": role_has_permission(current_user.role, "replenishment:create"),
            "today": "2024-05-31" if reference() else datetime.now(service.inventory_service.COMMERCIAL_ZONE).date().isoformat()}


def page():
    view = purchase_reference.view(request.args) if reference() else queries.list_orders(request.args)
    params = {**view["filters"], "per_page": view["pagination"]["per_page"]}
    if reference():
        params["ref"] = "1"
    view["query"] = urlencode(params)
    return render_template("purchases/page.html", **_context(view))


def approvals():
    from app.services import purchase_approval_service
    return render_template("purchases/approvals.html", view=purchase_approval_service.listing(request.args),
                           reference_mode=False, pro_shell=True)


def approvals_listing():
    from app.services import purchase_approval_service
    return jsonify(purchase_approval_service.listing(request.args))


def listing():
    return jsonify(purchase_reference.view(request.args) if reference() else queries.list_orders(request.args))


def detail_data(order_id):
    if reference():
        data = purchase_reference.detail(order_id)
        if data is None:
            abort(404)
        return data
    if not queries.available():
        raise ValidationError("Falta aplicar la migración de Compras.", status_code=503)
    return service.get_order(order_id).to_dict()


def detail(order_id):
    return jsonify(detail_data(order_id))


def options():
    if reference():
        return jsonify(suppliers=purchase_reference.SUPPLIERS, products=purchase_reference.PRODUCTS)
    if not queries.available():
        return jsonify(suppliers=[], products=[])
    return jsonify(queries.options(request.args.get("search", ""), request.args.get("supplier_id") or None,
                                   request.args.get("supplier_quote_id") or None))


def _mutate(fn, *args, status=200):
    _mutation_guard()
    try:
        validate_csrf(request.headers.get("X-CSRFToken"))
    except CsrfValidationError:
        raise ValidationError("Token CSRF inválido o ausente.", status_code=400) from None
    for attempt in range(DEADLOCK_RETRIES + 1):
        try:
            result = fn(*args)
            return jsonify(result.to_dict()), status
        except SQLAlchemyError as error:
            db.session.rollback()
            # InnoDB already rolled the victim back entirely and nothing is notified before commit,
            # so a deadlock is retried from scratch. Every other database error stops here.
            if _deadlock(error) and attempt < DEADLOCK_RETRIES:
                continue
            # Do not expose driver messages, schema or connection details.
            raise ValidationError("No se pudo guardar la operación. No se aplicaron cambios; intente de nuevo.", status_code=409)


# Gap locks under REPEATABLE READ can deadlock two inserts into the same index range.
DEADLOCK_RETRIES = 2


def _deadlock(error):
    args = getattr(getattr(error, "orig", None), "args", ())
    return bool(args) and args[0] == 1213


def create():
    return _mutate(service.create_order, _json_body(), status=201)


def edit(order_id):
    return _mutate(service.update_order, order_id, _json_body())


def duplicate(order_id):
    return _mutate(service.duplicate_order, order_id, status=201)


def cancel(order_id):
    return _mutate(service.cancel_order, order_id, _json_body())


def request_closure(order_id):
    return _mutate(service.request_closure, order_id, _json_body())


def approve_closure(order_id):
    return _mutate(lambda: service.decide_closure(order_id, _json_body(), approve=True))


def reject_closure(order_id):
    return _mutate(lambda: service.decide_closure(order_id, _json_body(), approve=False))


def receive(order_id):
    return _mutate(service.receive_order, order_id, _json_body())


def supplier_create():
    return _mutate(service.create_supplier, _json_body(), status=201)


def export():
    if reference():
        rows = purchase_reference.view(request.args)["all_items"]
    else:
        if not queries.available():
            raise ValidationError("Falta la migración de compras.", status_code=503)
        from app.services.purchase_export_service import bounded_rows
        rows = [row.to_dict(False) for row in bounded_rows(queries.relations(queries.query(queries.filters(request.args))).order_by(service.PurchaseOrder.id.desc()))]
    show_money = role_has_permission(current_user.role, "products:view_cost")
    records = [{"number": row["order_number"], "supplier": row["supplier"]["name"],
                "date": row["ordered_date"], "status": row["status_label"], "products": row["units"],
                "received": row["received_percent"], "pending_units": row["pending_units"],
                "not_supplied_units": row.get("quantity_not_supplied", 0),
                "estimated": row["estimated_delivery_date"] or ""} for row in rows]
    # "Por recibir" solo cuenta lo que una orden abierta espera; lo que faltó al cerrar va aparte.
    labels = [("number", "N° Orden"), ("supplier", "Proveedor"), ("date", "Fecha"),
              ("status", "Estado"), ("products", "Productos"), ("received", "Porcentaje recibido"),
              ("pending_units", "Unidades por recibir"), ("not_supplied_units", "Unidades no suministradas"),
              ("estimated", "Fecha estimada")]
    if show_money:
        for row, source in zip(records, rows):
            row["total"] = format_amount(source.get("total"), source.get("currency"))
            row["pending"] = format_amount(source.get("pending_amount"), source.get("currency"))
            row["not_supplied"] = format_amount(source.get("not_supplied_amount", "0.00"), source.get("currency"))
            row["currency"] = source.get("currency") or "No registrada"
        labels[5:5] = [("total", "Monto total"), ("pending", "Monto por recibir"),
                       ("not_supplied", "Monto no suministrado")]
        labels.append(("currency", "Moneda"))
    columns = [(key, label, 20) for key, label in labels]
    response = send_file(csv_file(records, columns, datetime.utcnow()), mimetype="text/csv; charset=utf-8", as_attachment=True, download_name="ordenes-de-compra.csv")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def print_order(order_id):
    return render_template("purchases/print.html", order=detail_data(order_id), reference_mode=reference(),
                           show_money=role_has_permission(current_user.role, "products:view_cost"))


def receptions():
    from app.controllers.reception_controller import page
    return page()


def suppliers_page():
    rows = purchase_reference.SUPPLIERS if reference() else [row.to_dict() for row in Supplier.query.order_by(Supplier.name).all()] if queries.available() else []
    return render_template("purchases/suppliers.html", rows=rows, **_context(None))


def approve(order_id):
    return _mutate(lambda: service.decide_order(order_id, _json_body(), approve=True))


def reject(order_id):
    return _mutate(lambda: service.decide_order(order_id, _json_body(), approve=False))
