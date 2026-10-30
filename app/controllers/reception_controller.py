"""Reception reads and documents; writes reuse purchase_controller.receive."""
from datetime import datetime
from urllib.parse import urlencode
from flask import abort, jsonify, render_template, request, send_file
from app.controllers.purchase_controller import reference, _context
from app.services import reception_query_service as queries, export_files
from app.utils import reception_reference as fixtures
from app.utils.purchase_currency import format_amount
from app.utils.permissions import response_includes_cost


def data(export=False):
    from app.services.purchase_query_service import available
    from app.services.exceptions import ValidationError
    if not reference() and not available():
        raise ValidationError("Recepciones no está disponible: falta aplicar la migración de Compras.", status_code=503)
    return fixtures.listing(request.args, export) if reference() else queries.listing(request.args, export)


def page():
    view = data()
    params = {**view['filters'], 'per_page': view['pagination']['per_page']}
    if reference():
        params['ref'] = '1'
    view['query'] = urlencode(params)
    context = _context(view)
    if reference():
        context['today'] = '2024-06-05'
    return render_template('purchases/receptions.html', **context, sorts=queries.sorts(), can_filter_amount=response_includes_cost())


def listing():
    return jsonify(data())


def options():
    orders = [fixtures.order()] if reference() else [row.to_dict(False) for row in queries.eligible().all()]
    return jsonify(orders=orders)


def order(order_id):
    if reference():
        if order_id != 43:
            abort(404)
        return jsonify(fixtures.order())
    row = queries.eligible().filter(queries.PO.id == order_id).first_or_404()
    return jsonify(row.to_dict())


def detail_data(receipt_id):
    from app.services.purchase_query_service import available
    from app.services.exceptions import ValidationError
    if not reference() and not available():
        raise ValidationError("Falta aplicar la migración de Compras.", status_code=503)
    row = fixtures.detail(receipt_id) if reference() else queries.detail(receipt_id)
    if row is None:
        abort(404)
    return row


def detail(receipt_id):
    return jsonify(detail_data(receipt_id))


def print_receipt(receipt_id):
    return render_template('purchases/reception_print.html', row=detail_data(receipt_id), show_money=response_includes_cost())


def export():
    fmt = export_files.validate_format(request.args.get('format', 'csv'))
    show_money = response_includes_cost()
    rows = [{**row, 'supplier': row['supplier']['name'],
             'amount': format_amount(row['amount'], row.get('currency')) if show_money else ''} for row in data(True)]
    columns = [('number', 'Número', 20), ('units', 'Productos recibidos', 20)]
    for key, label in [('supplier', 'Proveedor'), ('order_number', 'Orden de compra'), ('status_label', 'Estado'),
                       ('amount', 'Monto recibido'), ('received_at', 'Fecha de recepción')]:
        if request.args.get('include_' + key, '1') == '1' and (key != 'amount' or show_money):
            columns.append((key, label, 30))
    if show_money:
        columns.append(('currency', 'Moneda', 15))
    output, mime, filename = export_files.build(fmt, rows, columns, datetime.now(), sheet='Recepciones',
        title='Recepciones de compra', lines=['Todas las recepciones que coinciden con los filtros.'], stem='recepciones')
    return document(output, mime, filename)


def document(output, mime, filename):
    response = send_file(output, mimetype=mime, as_attachment=True, download_name=filename)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def pdf(receipt_id):
    row = detail_data(receipt_id)
    show_money = response_includes_cost()
    lines = [f"{row['order_number']} · {row['supplier']['name']}",
        f"Fecha: {row['received_at']} · Responsable: {row['responsible']} · {row['status_label']}",
        f"Unidades: {row['units']}", 'Notas: ' + row['notes']]
    if row.get('supplier_snapshot_available') is False:
        lines.append('Histórico sin copia del proveedor al aprobarse: datos actuales.')
    if row.get('corrections'):
        lines.append('El detalle original se conserva; el total incluye las correcciones aprobadas.')
        for correction in row['corrections']:
            lines.append(f"COR-{correction['id']} · {correction['status']} · {correction['reason']}")
            for item in correction['items']:
                lines.append(f"{item['product_name']}: {item['previous_quantity']} a {item['target_quantity']} · MOV-{item['movement_id'] or 'sin aplicar'}")
    columns = [('product_name', 'Producto', 60), ('product_code', 'Código', 25), ('quantity', 'Cantidad', 20),
               ('movement_id', 'Movimiento', 25)]
    items = row['items']
    if show_money:
        lines.append('Total recibido: ' + format_amount(row['amount'], row.get('currency')))
        columns[3:3] = [('unit_cost', 'Costo unitario', 25), ('subtotal', 'Subtotal', 25)]
        items = [{**item, 'unit_cost': format_amount(item['unit_cost'], row.get('currency')),
                  'subtotal': format_amount(item['subtotal'], row.get('currency'))} for item in items]
    return document(export_files.pdf_report('Recepción ' + row['number'], lines, items, columns),
                    'application/pdf', row['number'] + '.pdf')


def propose_correction(receipt_id):
    from app.controllers.purchase_controller import _mutate
    from app.controllers.sale_controller import _json_body
    from app.services import purchase_correction_service
    return _mutate(purchase_correction_service.propose, receipt_id, _json_body(), status=201)


def approve_correction(receipt_id, correction_id):
    return _decide_correction(receipt_id, correction_id, True)


def reject_correction(receipt_id, correction_id):
    return _decide_correction(receipt_id, correction_id, False)


def _decide_correction(receipt_id, correction_id, approve):
    from app.controllers.purchase_controller import _mutate
    from app.controllers.sale_controller import _json_body
    from app.services import purchase_correction_service
    return _mutate(lambda: purchase_correction_service.decide(receipt_id, correction_id, _json_body(), approve=approve))
