"""Reproduce the isolated thesis dataset review; stdout contains synthetic data only.

Uses the disposable SQLite fixture with PyMySQL blocked. No forecasting training,
application factory, environment files, real histories or persistent database.
"""
from collections import Counter
from datetime import date
from decimal import Decimal
import sys
import unicodedata
from unittest.mock import patch

from test_seed_tesis import SeedTests
import seed_tesis_safety as safety

AS_OF = date(2026, 9, 22)
TERMS = ('DEMO', 'DEMO-LARGE', 'Serie demo', '[DEMO', 'sintético', 'synthetic', 'seed', 'manifest', '20260921')


def table(headers, rows):
    def cell(value):
        return str(value).replace('|', '/').replace('\n', ' ')
    print('| ' + ' | '.join(headers) + ' |')
    print('|' + '|'.join('---' for _ in headers) + '|')
    for row in rows:
        print('| ' + ' | '.join(cell(value) for value in row) + ' |')
    print()


def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value).casefold())
                   if not unicodedata.combining(c))


def scan(domain):
    counts, hits, rows, cells = Counter(), [], 0, 0
    for name in safety.TABLES:
        for row in domain.db.session.execute(domain.db.metadata.tables[name].select()).mappings():
            rows += 1
            for field, value in row.items():
                cells += 1
                matches = [term for term in TERMS if value is not None and normalize(term) in normalize(value)]
                counts.update(matches)
                if matches:
                    hits.append((name, row['id'], field, matches))
    return counts, hits, rows, cells


def analyses(domain):
    from app.services import predictive_data_service as data, prediction_readiness_service as readiness
    from app.services import critical_product_service as risk, replenishment_service as replenishment
    from app.services import demand_analysis_service as descriptive

    products = domain.models.Product.query.order_by(domain.models.Product.id).all()
    series = data.load_series(today=AS_OF)
    with patch.object(descriptive, 'institutional_today', return_value=AS_OF):
        sufficiency = {r['product_id']: r for r in readiness.list_product_readiness()['items']}
    pending = replenishment.pending_supply_by_product()
    rows = []
    for product in products:
        history = series.get(product.id, data.TimeSeries(product.id, 'daily'))
        eligible = data.classify_eligibility(history, product.is_active)
        usable = eligible.status == data.ELIGIBLE or eligible.descriptive_allowed
        rate = sum(history.values) / history.periods if history.periods else 0
        operational = risk.assess(product.current_stock, product.minimum_stock, None, False)
        approximation = risk.assess(product.current_stock, product.minimum_stock, rate if usable else None, usable)
        recommendation = None
        if usable:
            recommendation = replenishment.compute(product.current_stock, product.minimum_stock, rate,
                pending.get(product.id, 0), product.purchase_price, AS_OF)
        rows.append(dict(product=product, history=history, eligibility=eligible, sufficiency=sufficiency[product.id],
                         operational=operational, risk=approximation, recommendation=recommendation))
    return rows


def print_summary(rows):
    sufficient = Counter(r['sufficiency']['sufficiency_class'] for r in rows)
    eligible = Counter(r['eligibility'].status for r in rows)
    forecast = sum(r['eligibility'].status == 'ELIGIBLE' or r['eligibility'].descriptive_allowed for r in rows)
    print('## Predicción, demanda e inventario\n')
    table(('Indicador', 'Cantidad'), [
        ('Productos', len(rows)), ('Historia suficiente (simple/avanzada)', sufficient['SIMPLE_READY'] + sufficient['ADVANCED_READY']),
        ('Forecast por modelo esperable', eligible['ELIGIBLE']), ('Estimación descriptiva esperable', forecast - eligible['ELIGIBLE']),
        ('Sin forecast/estimación', len(rows) - forecast),
        ('Agotados', sum(r['product'].current_stock == 0 for r in rows)),
        ('Positivo inferior al mínimo', sum(0 < r['product'].current_stock < r['product'].minimum_stock for r in rows)),
        ('Exactamente en el mínimo', sum(r['product'].current_stock == r['product'].minimum_stock for r in rows)),
        ('Reabastecimiento aproximado necesario', sum(bool(r['recommendation'] and r['recommendation'].needed) for r in rows)),
    ])
    table(('Suficiencia', 'Productos'), sorted(sufficient.items()))
    table(('Elegibilidad', 'Productos'), sorted(eligible.items()))
    table(('Patrón de demanda', 'Productos'), sorted(Counter(r['sufficiency']['demand_pattern_label'] for r in rows).items()))
    op, approx = Counter(r['operational'].label for r in rows), Counter(r['risk'].label for r in rows)
    table(('Riesgo', 'Operativo', 'Aproximación con media'), [(k, op[k], approx[k]) for k in ('Crítico', 'Alto', 'Medio', 'Bajo')])
    print('Sin entrenamiento ni selección de modelos. La aproximación usa demanda media por día calendario, '
          'las reglas de cobertura existentes y un plazo supuesto de 7 días; el reabastecimiento descuenta compras pendientes. '
          'Las cantidades finales pueden cambiar al ejecutar forecasting. Inactivo describe la demanda, no el estado del catálogo.\n')


def print_categories(domain, rows):
    print('## Categorías y ventas\n')
    grouped, amounts = [], []
    for category in domain.models.Category.query.order_by(domain.models.Category.id):
        lines = domain.models.SaleLine.query.join(domain.models.Product).filter(domain.models.Product.category_id == category.id).all()
        tickets = {line.sale_id for line in lines}
        independent = sum(all(line.product.category_id == category.id for line in domain.db.session.get(domain.models.Sale, sid).lines)
                          for sid in tickets)
        selected = [r for r in rows if r['product'].category_id == category.id]
        amounts.append(sum((line.line_total for line in lines), Decimal(0)))
        grouped.append([category.name, len(tickets), independent, len(lines),
                        sum(r['eligibility'].status == 'ELIGIBLE' for r in selected)])
    total = sum(amounts)
    table(('Categoría', 'Ventas con categoría', 'Ventas solo de categoría', 'Renglones', 'Elegibles modelo', '% importe'),
          [row + [f'{amount * 100 / total:.2f}'] for row, amount in zip(grouped, amounts)])
    print('Una venta puede incluir varias categorías; la columna de ventas con categoría no es sumable.\n')
    table(('Método de pago', 'Ventas'), sorted(Counter(s.payment_method for s in domain.models.Sale.query.all()).items()))


def print_products(rows):
    print('## Elegibilidad por producto (120)\n')
    table(('Código', 'Nombre', 'Categoría', 'Suficiencia', 'Demanda', 'Elegibilidad', 'Stock/mínimo', 'Riesgo aprox.'),
        [(r['product'].code, r['product'].name, r['product'].category.name, r['sufficiency']['sufficiency_class'],
          r['sufficiency']['demand_pattern_label'], r['eligibility'].status,
          f"{r['product'].current_stock}/{r['product'].minimum_stock}", r['risk'].label) for r in rows])
    print('## Reabastecimiento aproximado por producto\n')
    table(('Código', 'Nombre', 'Cantidad', 'Prioridad', 'Pendiente descontado'),
        [(r['product'].code, r['product'].name, r['recommendation'].suggested_quantity,
          r['recommendation'].priority_label, r['recommendation'].pending_supply)
         for r in rows if r['recommendation'] and r['recommendation'].needed])


def print_movements(domain):
    print('## Diez movimientos visibles\n')
    model = domain.models.StockMovement
    initial = model.query.filter(model.reason.startswith('Existencia inicial')).order_by(model.id).all()
    receipts = model.query.filter(model.reason.startswith('Recepci')).order_by(model.id).all()
    exits = model.query.filter_by(movement_type='salida').order_by(model.id).all()
    adjustments = model.query.filter_by(movement_type='ajuste').order_by(model.id).all()
    selected = [initial[0], initial[60], receipts[-20], receipts[-10], receipts[-1], exits[-30], exits[-1],
                next(m for m in reversed(exits) if m.new_stock == 0), adjustments[-2], adjustments[-1]]
    table(('Fecha', 'Tipo', 'Producto', 'Cantidad', 'Anterior', 'Actual', 'Motivo', 'Responsable', 'Activo hoy'),
        [(m.created_at.date(), m.movement_type, m.product.code, m.quantity, m.previous_stock, m.new_stock,
          m.reason, m.user.name, m.user.is_active) for m in selected])
    table(('Responsable', 'Rol', 'Activo', 'Movimientos'),
          [(u.name, u.role, u.is_active, model.query.filter_by(user_id=u.id).count())
           for u in domain.models.User.query.order_by(domain.models.User.id)])


def print_documents(domain):
    print('## Proveedores y clientes: cinco ejemplos de cada uno\n')
    suppliers = domain.models.Supplier.query.order_by(domain.models.Supplier.id).all()
    customers = domain.models.Customer.query.order_by(domain.models.Customer.id).all()
    table(('Proveedor', 'Cliente'), [(suppliers[i].name, customers[i * 11].name) for i in range(5)])
    print('## Cinco cotizaciones\n')
    quotes = domain.models.Quote.query.order_by(domain.models.Quote.id).all()
    selected = [next(q for q in quotes if q.effective_status(AS_OF) == state)
                for state in ('pending', 'sent', 'accepted', 'rejected', 'expired')]
    table(('Número', 'Cliente', 'Estado', 'Validez', 'Total'),
          [(q.quote_number, q.customer.name, q.effective_status(AS_OF), q.valid_until, q.total) for q in selected])
    print('## Cinco pedidos\n')
    orders = domain.models.Order.query.order_by(domain.models.Order.id).all()
    table(('Número', 'Cliente', 'Estado', 'Pago', 'Total'),
          [(o.order_number, o.customer.name, o.status, o.payment_status, o.total) for o in orders[:5]])
    print('## Cinco órdenes de compra\n')
    purchases = domain.models.PurchaseOrder.query.order_by(domain.models.PurchaseOrder.id).all()
    selected = [purchases[0], next(o for o in purchases if o.supplier_id != purchases[0].supplier_id)]
    selected += [next(o for o in purchases if o.status == state) for state in ('pending', 'partial', 'cancelled')]
    table(('Número', 'Proveedor', 'Estado', 'Ordenado', 'Recibido', 'Total'),
          [(o.order_number, o.supplier.name, o.status, sum(l.quantity_ordered for l in o.lines),
            sum(l.quantity_received for l in o.lines), o.total) for o in selected])
    print('Importes almacenados, sin atribuirles una moneda. Todos los nombres y operaciones son ficticios.\n')


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        SeedTests.setUpClass()
        domain = SeedTests.domain
        rows = analyses(domain)
        counts, hits, record_count, cells = scan(domain)
        print(f'# Simulación aislada del conjunto de tesis — {AS_OF}\n')
        print('100 % sintético; no es evidencia empírica. Generado de nuevo mediante los servicios reales en SQLite '
              'temporal desechable, con PyMySQL bloqueado. No se creó la base MySQL ni se ejecutó el seed real.\n')
        table(('Entidad', 'Registros'), [(k, len(v)) for k, v in SeedTests.manifest['payload']['rows'].items()])
        print_summary(rows)
        print_categories(domain, rows)
        print_products(rows)
        print_movements(domain)
        print_documents(domain)
        print('## Búsqueda completa de etiquetas\n')
        print(f'{len(safety.TABLES)} tablas, {record_count} registros, {cells} campos. '
              'Solo se excluye seed_manifests. Búsqueda sin distinguir mayúsculas ni acentos.\n')
        table(('Expresión', 'Coincidencias'), [(term, counts[term]) for term in TERMS])
        safety.require(not hits, 'Se encontraron etiquetas prohibidas en datos comerciales.')
    finally:
        SeedTests.doClassCleanups()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
