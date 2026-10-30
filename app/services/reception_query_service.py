"""Read-only projections over the canonical purchase reception domain."""
from datetime import datetime, timezone

from sqlalchemy import case, func, or_
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import User, StockMovement, Product
from app.models.purchase import PurchaseOrder as PO, PurchaseOrderLine as POL, PurchaseReception as R, PurchaseReceptionLine as RL, Supplier
from app.services.purchase_service import day, integer, money
from app.services.inventory_service import COMMERCIAL_ZONE
from app.services.exceptions import ValidationError, NotFoundError

SORTS = {'date_desc': 'Fecha de recepción (más reciente)', 'date_asc': 'Fecha de recepción (más antigua)',
         'amount_desc': 'Mayor monto recibido', 'amount_asc': 'Menor monto recibido',
         'number_asc': 'Número ascendente', 'number_desc': 'Número descendente'}


def filters(params):
    values = {key: str(params.get(key, '')).strip() for key in
              ('search', 'status', 'supplier_id', 'start', 'end', 'responsible_id', 'min_amount', 'max_amount')}
    values['search'] = values['search'][:150]
    values['sort'] = params.get('sort', 'date_desc')
    if values['sort'] not in SORTS or values['status'] not in ('', 'partial', 'completed'):
        raise ValidationError('Estado u ordenación inválidos.')
    for key in ('supplier_id', 'responsible_id'):
        if values[key]:
            values[key] = integer(values[key], key)
    for key in ('start', 'end'):
        day(values[key], key, optional=True)
    for key in ('min_amount', 'max_amount'):
        if values[key]:
            money(values[key])
    if values['start'] and values['end'] and values['start'] > values['end']:
        raise ValidationError('La fecha inicial supera la final.')
    if values['min_amount'] and values['max_amount'] and money(values['min_amount']) > money(values['max_amount']):
        raise ValidationError('El monto mínimo supera el máximo.')
    return values


def projection():
    totals = db.session.query(RL.reception_id.label('id'), func.sum(RL.quantity).label('units'),
        func.sum(RL.quantity * POL.unit_cost).label('amount')).join(POL, POL.id == RL.purchase_order_line_id).group_by(RL.reception_id).subquery()
    ordered = db.session.query(POL.purchase_order_id.label('order_id'), func.sum(POL.quantity_ordered).label('ordered')).group_by(POL.purchase_order_id).subquery()
    events = db.session.query(R.id, totals.c.units, totals.c.amount, ordered.c.ordered,
        func.sum(totals.c.units).over(partition_by=R.purchase_order_id,
            order_by=(R.received_at, R.created_at, R.id), rows=(None, 0)).label('cumulative'))\
        .join(totals, totals.c.id == R.id).join(ordered, ordered.c.order_id == R.purchase_order_id).subquery()
    return db.session.query(events.c.id, events.c.units, events.c.amount,
        case((events.c.cumulative >= events.c.ordered, 'completed'), else_='partial').label('status')).subquery()


def query(values):
    p = projection()
    result = db.session.query(R, p.c.units, p.c.amount, p.c.status).join(p, p.c.id == R.id).join(PO).join(Supplier)
    if values['search']:
        needle = values['search'].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        match = '%' + needle + '%'
        result = result.filter(or_(func.concat('REC-', func.lpad(R.id, func.greatest(5, func.length(R.id)), '0')).ilike(match, escape='\\'),
            PO.order_number.ilike(match, escape='\\'), Supplier.name.ilike(match, escape='\\'),
            R.lines.any(RL.purchase_order_line_id.in_(db.session.query(POL.id).filter(or_(
                POL.product_name.ilike(match, escape='\\'), POL.product_code.ilike(match, escape='\\')))))))
    for key, column in (('status', p.c.status), ('supplier_id', PO.supplier_id), ('responsible_id', R.received_by_user_id)):
        if values[key]:
            result = result.filter(column == values[key])
    for key, column, lower in (('start', R.received_at, True), ('end', R.received_at, False),
                               ('min_amount', p.c.amount, True), ('max_amount', p.c.amount, False)):
        if values[key]:
            result = result.filter(column >= values[key] if lower else column <= values[key])
    sort = values['sort']
    column = p.c.amount if sort.startswith('amount') else R.id if sort.startswith('number') else R.received_at
    return result.order_by(column.desc() if sort.endswith('desc') else column.asc(),
                           R.created_at.desc() if sort.endswith('desc') else R.created_at.asc(),
                           R.id.desc() if sort.endswith('desc') else R.id.asc())


def relations(result):
    return result.options(joinedload(R.order).joinedload(PO.supplier), joinedload(R.responsible),
                          joinedload(R.order).selectinload(PO.lines), selectinload(R.lines))


def serialize(record, detail=False):
    from app.utils.permissions import response_includes_cost
    receipt, units, amount, status = record
    show_money = response_includes_cost()
    row = {'id': receipt.id, 'number': f'REC-{receipt.id:05d}', 'order_id': receipt.purchase_order_id,
        'order_number': receipt.order.order_number, 'supplier': receipt.order.supplier.to_dict(),
        'currency': receipt.order.currency,
        'received_at': receipt.received_at.isoformat(), 'responsible': receipt.responsible.name,
        'responsible_id': receipt.received_by_user_id, 'units': int(units),
        'amount': f'{amount:.2f}' if show_money else None,
        'status': status, 'status_label': 'Completada' if status == 'completed' else 'Parcial'}
    if detail:
        lines = {line.id: line for line in receipt.order.lines}
        row.update(notes=receipt.notes or '', created_at=receipt.created_at.replace(tzinfo=timezone.utc).astimezone(COMMERCIAL_ZONE).isoformat(),
            items=[{**lines[line.purchase_order_line_id].to_dict(), 'quantity': line.quantity,
                **({'subtotal': f'{line.quantity * lines[line.purchase_order_line_id].unit_cost:.2f}'} if show_money else {}),
                'movement_id': line.stock_movement_id} for line in receipt.lines])
    return row


def detail(receipt_id):
    record = relations(query(filters({})).filter(R.id == receipt_id)).first()
    if record is None:
        raise NotFoundError('Recepción no encontrada.')
    row = serialize(record, True)
    ids = [line['movement_id'] for line in row['items']]
    from app.services.inventory_service import movement_data, movement_presentations

    movements = StockMovement.query.options(joinedload(StockMovement.product).joinedload(Product.category),
                                             joinedload(StockMovement.user)).filter(StockMovement.id.in_(ids)).order_by(StockMovement.id).all()
    presentations = movement_presentations(movements)
    row['movements'] = [movement_data(movement, presentations[movement.id]) for movement in movements]
    return row


def eligible():
    return PO.query.filter(PO.status == 'approved', ~PO.receptions.any()).order_by(PO.id.desc())


def kpis():
    p = projection()
    count, completed = db.session.query(func.count(p.c.id), func.sum(case((p.c.status == 'completed', 1), else_=0))).one()
    today = datetime.now(COMMERCIAL_ZONE).date()
    from app.utils.permissions import response_includes_cost
    totals = (db.session.query(PO.currency, func.sum(p.c.amount))
              .join(R, R.id == p.c.id).join(PO, PO.id == R.purchase_order_id)
              .filter(R.received_at >= today.replace(day=1), R.received_at <= today, PO.currency.in_(('USD', 'VES')))
              .group_by(PO.currency).order_by(PO.currency).all()) if response_includes_cost() else []
    return {'count': count, 'completed': int(completed or 0), 'pending': eligible().count(), 'amount': None,
            'totals_by_currency': [{'currency': currency, 'amount': f'{value:.2f}'} for currency, value in totals],
            'comparison': 'Sin comparación disponible', 'completed_sub': f'{completed * 100 / count:.1f}% del total' if count else 'Sin recepciones'}


def pagination(params, count):
    size = integer(params.get('per_page', 8), 'Por página')
    if size not in (8, 16, 24):
        raise ValidationError('Seleccione 8, 16 o 24 por página.')
    pages = max(1, (count + size - 1) // size)
    page = min(integer(params.get('page', 1), 'Página'), pages)
    return {'page': page, 'per_page': size, 'pages': pages, 'total': count,
            'numbers': sorted({1, pages, *range(max(1, page-2), min(pages, max(5, page+2))+1)}),
            'from': (page-1)*size+1 if count else 0, 'to': min(page*size, count)}


def listing(params, export=False):
    values = filters(params)
    result = query(values)
    if export:
        return [serialize(row) for row in relations(result).all()]
    page = pagination(params, result.count())
    rows = relations(result).offset((page['page']-1)*page['per_page']).limit(page['per_page']).all()
    return {'filters': values, 'pagination': page, 'items': [serialize(row) for row in rows], 'kpis': kpis(),
        'suppliers': [row.to_dict() for row in Supplier.query.order_by(Supplier.name).all()],
        'responsibles': [{'id': row.id, 'name': row.name} for row in User.query.join(R, R.received_by_user_id == User.id).distinct().order_by(User.name).all()]}
