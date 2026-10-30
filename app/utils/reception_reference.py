"""Deterministic screenshot fixtures; never queries a database."""
from copy import deepcopy
from app.services.reception_query_service import filters, pagination

NAMES = ['Ferremax, C.A.', 'Aceros del Centro, C.A.', 'Materiales Premier, C.A.', 'Construcción Total, C.A.',
         'Distribuidora Industrial, C.A.', 'Suministros Ferreteros, C.A.', 'Cemento Plus, C.A.', 'Insumos del Norte, C.A.']
SUPPLIERS = [{'id': i+1, 'name': name} for i, name in enumerate(NAMES)]


def order():
    products = [('Cemento Gris Portland 42.5 kg', 'CEM-001', 100, '8.50'),
                ('Varilla de Hierro 1/2” x 12m', 'VAR-004', 50, '12.30'),
                ('Arena de Río Granel', 'ARE-002', 200, '4.75'), ('Bloque de Concreto 15cm', 'BLO-001', 300, '1.20')]
    return {'id': 43, 'currency': 'USD', 'order_number': 'OC-000043', 'supplier': SUPPLIERS[0], 'ordered_date': '2024-06-01',
        'items': [{'id': i+1, 'product_name': name, 'product_code': code, 'quantity_ordered': qty,
                   'quantity_received': 0, 'quantity_pending': qty, 'unit_cost': cost} for i, (name, code, qty, cost) in enumerate(products)]}


def rows():
    amounts = ['58250.00', '46780.00', '31920.00', '22340.00', '12450.00', '18400.00', '8950.00', '9160.00']
    dates = ['2024-06-04', '2024-06-02', '2024-05-28', '2024-05-30', '2024-05-25', '2024-05-31', '2024-05-22', '2024-05-26']
    base = [{'id': 98-i, 'number': f'REC-{98-i:05d}', 'order_id': 42-i, 'order_number': f'OC-{42-i:05d}',
             'supplier': SUPPLIERS[i], 'received_at': dates[i], 'responsible': 'Administrador', 'responsible_id': 1,
             'currency': 'USD', 'units': [12, 8, 15, 6, 10, 9, 5, 7][i], 'amount': amounts[i],
             'status': 'partial' if i == 5 else 'completed', 'status_label': 'Parcial' if i == 5 else 'Completada'} for i in range(8)]
    extra = []
    for i in range(120):
        receipt_id = 90-i if i < 90 else 1000+i
        extra.append({**deepcopy(base[i % 8]), 'id': receipt_id, 'number': f'REC-{receipt_id:05d}', 'received_at': '2024-05-20'})
    return base + extra


def listing(params, export=False):
    values = filters(params)
    data = rows()
    if values['search']:
        data = [row for row in data if values['search'].casefold() in str(row).casefold()]
    for key, field in [('status', 'status'), ('responsible_id', 'responsible_id')]:
        if values[key]:
            data = [row for row in data if str(row[field]) == str(values[key])]
    if values['supplier_id']:
        data = [row for row in data if row['supplier']['id'] == values['supplier_id']]
    for key, field, lower in [('start', 'received_at', True), ('end', 'received_at', False), ('min_amount', 'amount', True), ('max_amount', 'amount', False)]:
        if values[key]:
            value = float(values[key]) if field == 'amount' else values[key]
            data = [row for row in data if ((float(row[field]) if field == 'amount' else row[field]) >= value if lower else (float(row[field]) if field == 'amount' else row[field]) <= value)]
    if 'sort' in params:
        key = 'amount' if values['sort'].startswith('amount') else 'id' if values['sort'].startswith('number') else 'received_at'
        data.sort(key=lambda row: float(row[key]) if key == 'amount' else row[key], reverse=values['sort'].endswith('desc'))
    if export:
        return data
    page = pagination(params, len(data))
    values['start'] = params.get('start', '2024-05-01')
    values['end'] = params.get('end', '2024-05-31')
    visible = data[(page['page']-1)*page['per_page']:page['page']*page['per_page']]
    return {'filters': values, 'items': visible, 'pagination': page, 'suppliers': deepcopy(SUPPLIERS),
        'responsibles': [{'id': 1, 'name': 'Administrador'}], 'kpis': {'count': 128, 'completed': 97, 'pending': 31,
        'amount': '1245680.00', 'totals_by_currency': [{'currency': 'USD', 'amount': '1245680.00'}],
        'comparison': 'Este mes   ↑ 15.6%', 'completed_sub': '75.8% del total'}}


def detail(receipt_id):
    row = next((row for row in rows() if row['id'] == receipt_id), None)
    if row:
        row.update(notes='Recepción de referencia visual.', created_at=row['received_at']+'T10:00:00-04:00', items=[], movements=[])
    return row
