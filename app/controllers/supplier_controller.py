"""Supplier HTTP presentation; one live domain and an isolated read-only preview."""
from datetime import datetime
from app.utils.purchase_currency import format_amount
from urllib.parse import urlencode

from flask import abort, jsonify, render_template, request, send_file
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models.purchase import STATUS_LABELS
from app.controllers.purchase_controller import reference, _mutation_guard, _context
from app.controllers.sale_controller import _json_body
from app.services import supplier_service as service, supplier_query_service as queries, export_files
from app.services.exceptions import ValidationError
from app.utils import supplier_reference
from app.utils.permissions import response_includes_cost, role_has_permission


def require_schema():
    if not reference() and not queries.available():
        raise ValidationError("Proveedores no está disponible: falta aplicar la migración de proveedores.", status_code=503)


def view_data():
    return supplier_reference.listing(request.args) if reference() else queries.listing(request.args)


def page():
    view = view_data()
    params = {**view["filters"], "per_page": view["pagination"]["per_page"]}
    if reference():
        params["ref"] = "1"
        if "sort" not in request.args:
            params.pop("sort")
    view["query"] = urlencode(params)
    sorts = {key: label for key, label in queries.SORTS.items()
             if response_includes_cost() or not key.startswith("total")}
    context = _context(view)
    context.update(can_write=role_has_permission(current_user.role, "suppliers:propose"),
                   can_approve=role_has_permission(current_user.role, "suppliers:approve"),
                   purchase_status_labels=STATUS_LABELS)
    return render_template("suppliers/page.html", sorts=sorts, **context)


def listing():
    return jsonify(view_data())


def detail(supplier_id):
    require_schema()
    data = supplier_reference.detail(supplier_id) if reference() else queries.detail(supplier_id)
    if data is None:
        abort(404)
    return jsonify(data)


def mutate(fn, *args, status=200):
    _mutation_guard()
    require_schema()
    try:
        result = fn(*args)
        return jsonify(result if isinstance(result, dict) else queries.detail(result.id)), status
    except SQLAlchemyError:
        db.session.rollback()
        raise ValidationError("No se pudo guardar el proveedor. No se aplicaron cambios.", status_code=409)


def create():
    return mutate(service.save, _json_body(), status=201)


def approve(supplier_id):
    return mutate(lambda: service.decide(supplier_id, approve=True))


def reject(supplier_id):
    reason = _json_body().get("reason")
    return mutate(lambda: service.decide(supplier_id, approve=False, reason=reason))


def edit(supplier_id):
    return mutate(service.save, _json_body(), supplier_id)


def propose_status(supplier_id):
    return mutate(service.propose_status, supplier_id, _json_body())


def preferred(supplier_id):
    return mutate(service.change, supplier_id, "preferred")


def status(supplier_id):
    return mutate(service.change, supplier_id, "status")


def delete(supplier_id):
    return mutate(service.change, supplier_id, "delete")


def export():
    require_schema()
    format_name = export_files.validate_format(request.args.get("format", "csv"))
    from app.services.purchase_export_service import bounded_rows
    rows = supplier_reference.listing(request.args)["all_items"] if reference() else [queries.serialize(row) for row in bounded_rows(queries.query(queries.filters(request.args)))]
    columns = [("name", "Proveedor", 30), ("tax_id", "RIF", 18)]
    options = {"contact": [(key, label, 22) for key, label in (("contact_name", "Contacto"), ("contact_title", "Cargo"), ("phone", "Teléfono"), ("email", "Correo"))],
               "status": [("status_label", "Estado", 12)], "category": [("category", "Categoría", 18)],
               "total": [("total_usd", "Compras USD", 20), ("total_ves", "Compras VES", 20),
                         ("unknown_currency_orders", "Órdenes sin moneda", 18)],
               "last": [("last", "Última compra", 18)]}
    for option, extra in options.items():
        if option == "total" and not response_includes_cost():
            continue
        if request.args.get("include_" + option, "1") == "1":
            columns.extend(extra)
    for row in rows:
        row["status_label"] = "Activo" if row["is_active"] else "Inactivo"
        if response_includes_cost():
            amounts = {value['currency']: value['amount'] for value in row['totals_by_currency']}
            row['total_usd'] = format_amount(amounts.get('USD'), 'USD')
            row['total_ves'] = format_amount(amounts.get('VES'), 'VES')
            row.setdefault('unknown_currency_orders', 0)
    now = datetime(2024, 5, 31) if reference() else datetime.utcnow()
    output, mime, filename = export_files.build(format_name, rows, columns, now, sheet="Proveedores", title="Proveedores",
        lines=[("REFERENCIA VISUAL · " if reference() else "") + f"{len(rows)} proveedores · Todas las coincidencias"], stem="proveedores")
    response = send_file(output, mimetype=mime, as_attachment=True, download_name=filename)
    response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
    return response
