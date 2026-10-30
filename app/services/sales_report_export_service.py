"""Exportación CSV/PDF del reporte de ventas (SOLO LECTURA, mismo período y filtro de la pantalla).

Reutiliza ``sales_report_service.report`` (todas las filas, sin paginar) y la infraestructura de
exportación existente: ``csv_file`` (BOM UTF-8, literales seguros ante fórmulas) y ``CatalogPDF``
(fuentes core cp1252, pie "Página N"). El PDF es un informe compuesto —portada con KPIs, tablas
con encabezados repetidos y barras dibujadas—, no una captura de la web.
"""
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO

from fpdf.fonts import FontFace

from app.services import sales_report_service
from app.services.exceptions import ValidationError
from app.services.export_files import MAX_EXPORT_ROWS, MIMES, csv_file
from app.services.inventory_service import COMMERCIAL_ZONE
from app.services.product_export_service import CatalogPDF, pdf_text

TITLES = {'summary': 'Resumen de Ventas', 'products': 'Reporte de Ventas por Producto', 'customers': 'Reporte de Ventas por Cliente',
          'categories': 'Reporte de Ventas por Categoría', 'payments': 'Reporte de Métodos de Pago'}
STEMS = {'summary': 'resumen', 'products': 'productos', 'customers': 'clientes', 'categories': 'categorias', 'payments': 'metodos_pago'}
# (clave, etiqueta legible, ancho en mm para el PDF vertical A4: 190 mm útiles)
COLUMNS = {
    'products': [('n', '#', 8), ('name', 'Producto', 60), ('code', 'Código', 24), ('category', 'Categoría', 36), ('units', 'Unidades', 18),
                 ('revenue', 'Ingresos', 24), ('percent', 'Participación (%)', 20)],
    'customers': [('n', '#', 8), ('name', 'Cliente', 50), ('tax_id', 'RIF', 26), ('sales', 'Compras', 16), ('units', 'Productos (unidades)', 22),
                  ('revenue', 'Total', 24), ('average', 'Ticket promedio', 24), ('status', 'Estado', 20)],
    'categories': [('n', '#', 8), ('name', 'Categoría', 56), ('products', 'Productos vendidos', 26), ('units', 'Unidades', 20), ('revenue', 'Ingresos', 28),
                   ('percent', 'Participación (%)', 26), ('change_percent', 'Variación (%)', 26)],
    'payments': [('n', '#', 10), ('name', 'Método', 60), ('count', 'Transacciones', 30), ('revenue', 'Monto total', 30), ('average', 'Ticket promedio', 30),
                 ('percent', 'Participación (%)', 30)],
    'summary': [('section', 'Sección', 40), ('concept', 'Concepto', 60), ('value', 'Valor', 40), ('detail', 'Detalle', 50)],
}
KPI_LABELS = {
    'summary': [('revenue', 'Ventas del período'), ('count', 'Ventas realizadas'), ('average', 'Ticket promedio'), ('customers', 'Clientes activos')],
    'products': [('products', 'Productos vendidos'), ('units', 'Unidades vendidas'), ('revenue', 'Ingresos por producto'), ('average', 'Ticket prom. por producto')],
    'customers': [('active', 'Clientes activos'), ('recurring', 'Clientes recurrentes (%)'), ('revenue', 'Ingresos por cliente'), ('average', 'Ticket promedio')],
    'categories': [('active', 'Categorías activas'), ('revenue', 'Ingresos por categoría'), ('leader', 'Categoría líder'), ('growth', 'Crecimiento prom. (%)')],
    'payments': [('active', 'Métodos activos'), ('revenue', 'Ingresos cobrados'), ('main', 'Método principal'), ('average', 'Ticket prom. por método')],
}
MONEY = {'revenue', 'average', 'total', 'previous_revenue', 'average_unit'}
MONTHS_SHORT = ('Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic')
EMPTY = 'Sin ventas completadas en el período.'
BLUE, INK, MUTED, LINE, PANEL = (8, 102, 245), (16, 24, 50), (101, 113, 141), (228, 232, 240), (245, 247, 251)


def _money(value) -> str:
    return f"$ {Decimal(value):,.2f}"


def _range(start, end) -> str:
    return f"{start:%d/%m/%Y} - {end:%d/%m/%Y}"


def _stamp(sold_at: datetime) -> str:
    """sold_at se guarda en UTC naive; se muestra en hora comercial."""
    return sold_at.replace(tzinfo=timezone.utc).astimezone(COMMERCIAL_ZONE).strftime('%d/%m/%Y %H:%M')


def _pct(value) -> str:
    return f"{value:g}%" if value is not None else "—"


def _cell(key, value):
    """Valor CSV: dinero/porcentajes numéricos, None vacío, texto tal cual (csv_file lo protege)."""
    if value is None:
        return ''
    if key in MONEY:
        return Decimal(value)
    return value


def _rows(tab: str, data: dict) -> list[dict]:
    rows = []
    for n, r in enumerate(data['rows'], 1):
        row = {'n': n, **r}
        if tab == 'customers':
            row['status'] = 'Activo' if r['is_active'] else 'Inactivo'
        rows.append({key: _cell(key, row.get(key)) for key, _, _ in COLUMNS[tab]})
    return rows


def _kpi_text(key, value) -> str:
    if key in ('leader', 'main'):
        return value['name'] if value else '—'
    if key in ('recurring', 'growth'):
        return _pct(value)
    if key in ('revenue', 'average'):
        return _money(value or 0)
    return f"{int(value):,}"


def _kpis(tab, data) -> list[tuple]:
    """(etiqueta, valor, variación) por KPI; variación solo con cierre real del período anterior."""
    items = []
    for key, label in KPI_LABELS[tab]:
        value, comparison = data['kpis'][key]
        if comparison and comparison['reason'] == 'ok':
            pct = comparison['change_percent']
            trend = f"{'+' if pct > 0 else ''}{pct:g}% vs. período anterior"
        elif comparison and comparison['reason'] == 'new':
            trend = 'Nuevo en este período'
        elif key in ('leader', 'main') and value:
            trend = f"{_pct(value['percent'])} del total"
        else:
            trend = 'Sin período anterior comparable'
        items.append((label, _kpi_text(key, value), trend))
    return items


def _summary_rows(data: dict) -> list[dict]:
    """Resumen aplanado: una fila por concepto real (KPIs, meses, categorías, top, ejecutivo)."""
    rows = [{'section': 'Indicadores', 'concept': label, 'value': value, 'detail': trend} for label, value, trend in _kpis('summary', data)]
    m = data['monthly']
    for month, current, previous in zip(m['months'], m['current'], m['previous_year']):
        rows.append({'section': 'Tendencia mensual', 'concept': month[:7], 'value': '' if current is None else Decimal(current),
                     'detail': '' if previous is None else f"Año anterior: {Decimal(previous):,.2f}"})
    for item in data['by_category']['items']:
        rows.append({'section': 'Ventas por categoría', 'concept': item['name'], 'value': Decimal(item['revenue']), 'detail': _pct(item['percent'])})
    for n, p in enumerate(data['top_products'], 1):
        rows.append({'section': 'Top productos', 'concept': f"{n}. {p['name']}", 'value': p['revenue'], 'detail': f"{p['units']} unidades"})
    for n, c in enumerate(data['top_customers'], 1):
        rows.append({'section': 'Top clientes', 'concept': f"{n}. {c['name']}", 'value': Decimal(c['revenue']), 'detail': f"{c['sales']} compras"})
    for label, value in _executive(data):
        rows.append({'section': 'Resumen ejecutivo', 'concept': label, 'value': value, 'detail': ''})
    return rows


def _executive(data) -> list[tuple]:
    e = data['executive']
    labels = dict(KPI_LABELS['summary'])
    return [('Mejor categoría', e['category']['name'] if e['category'] else '—'),
            ('Mayor crecimiento', f"{labels[e['growth'][0]]} ({'+' if e['growth'][1] > 0 else ''}{e['growth'][1]:g}%)" if e['growth'] else 'Sin período anterior comparable'),
            ('Mayor ticket', e['ticket']['name'] if e['ticket'] else '—'),
            ('Mayor producto', e['product']['name'] if e['product'] else '—'),
            ('Método principal', f"{e['payment']['name']} ({e['payment']['count']:,} ventas)" if e['payment'] else '—')]


# --- PDF -------------------------------------------------------------------------------

class ReportPDF(CatalogPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 10)
        self.set_text_color(*BLUE)
        self.cell(95, 6, 'Ferretería y Construcciones El Conejo C.A.')
        self.set_font('Helvetica', '', 8)
        self.set_text_color(*MUTED)
        self.cell(95, 6, 'Sistema de Inventario', align='R', new_x='LMARGIN', new_y='NEXT')
        self.set_draw_color(*LINE)
        self.line(10, self.get_y() + 1, 200, self.get_y() + 1)
        self.ln(4)
        self.set_text_color(*INK)


def _fmt(key, value) -> str:
    if value is None or value == '':
        return '—'
    if key in MONEY or (key == 'value' and isinstance(value, Decimal)):
        return _money(value)
    if key in ('percent', 'change_percent'):
        return _pct(value)
    if isinstance(value, int):
        return f"{value:,}"
    return ' '.join(pdf_text(value).split())


def _heading(pdf, text):
    if pdf.will_page_break(16):
        pdf.add_page()
    pdf.ln(3)
    pdf.set_font('Helvetica', 'B', 12)
    pdf.set_text_color(*INK)
    pdf.cell(0, 8, pdf_text(text), new_x='LMARGIN', new_y='NEXT')
    pdf.set_draw_color(*BLUE)
    pdf.set_line_width(0.5)
    pdf.line(10, pdf.get_y(), 40, pdf.get_y())
    pdf.set_line_width(0.2)
    pdf.ln(2)


def _paragraph(pdf, text):
    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(0, 5, pdf_text(text), new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(*INK)


def _table(pdf, columns, rows, align_right=()):
    if not rows:
        return _paragraph(pdf, EMPTY)
    pdf.set_font('Helvetica', '', 8)
    pdf.set_fill_color(0)  # fpdf2 hereda el relleno vigente en las celdas; las barras dejan azul
    pdf.set_draw_color(*LINE)
    pdf.set_text_color(*INK)
    with pdf.table(col_widths=[w for _, _, w in columns], line_height=5, padding=1, text_align=['R' if k in align_right else 'L' for k, _, _ in columns],
                   headings_style=FontFace(emphasis='BOLD', color=INK, fill_color=PANEL), borders_layout='HORIZONTAL_LINES', wrapmode='CHAR') as table:
        table.row([label for _, label, _ in columns])
        for data in rows:
            table.row([_fmt(key, data.get(key)) for key, _, _ in columns])


def _bars(pdf, items):
    """Barras horizontales reales: (nombre, texto del valor, valor numérico)."""
    if not items:
        return _paragraph(pdf, EMPTY)
    top = max(Decimal(v) for _, _, v in items) or Decimal(1)
    pdf.set_font('Helvetica', '', 8)
    for name, text, value in items:
        if pdf.will_page_break(7):
            pdf.add_page()
        y = pdf.get_y()
        pdf.cell(62, 6, ' '.join(pdf_text(name).split())[:42])
        pdf.set_fill_color(*PANEL)
        pdf.rect(72, y + 1.5, 90, 3, 'F')
        pdf.set_fill_color(*BLUE)
        pdf.rect(72, y + 1.5, float(Decimal(value) * 90 / top), 3, 'F')
        pdf.set_x(164)
        pdf.cell(26, 6, pdf_text(text), align='R', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(1)


def _kpi_boxes(pdf, items):
    x0, y0, w, h = 10, pdf.get_y(), 45.25, 20
    for n, (label, value, trend) in enumerate(items):
        x = x0 + n * (w + 3)
        pdf.set_fill_color(*PANEL)
        pdf.set_draw_color(*LINE)
        pdf.rect(x, y0, w, h, 'DF')
        pdf.set_xy(x + 2, y0 + 2)
        pdf.set_font('Helvetica', '', 7)
        pdf.set_text_color(*MUTED)
        pdf.cell(w - 4, 4, pdf_text(label))
        pdf.set_xy(x + 2, y0 + 7)
        pdf.set_font('Helvetica', 'B', 11)
        pdf.set_text_color(*INK)
        pdf.cell(w - 4, 6, pdf_text(value))
        pdf.set_xy(x + 2, y0 + 13.5)
        pdf.set_font('Helvetica', '', 6.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(w - 4, 4, pdf_text(trend))
    pdf.set_text_color(*INK)
    pdf.set_y(y0 + h + 4)


def _evolution_table(pdf, evolution, money=True):
    """Serie mensual (12 meses) de las principales series: meses en filas, series en columnas."""
    series = evolution['series']
    if not series:
        return _paragraph(pdf, EMPTY)
    width = (190 - 22) / len(series)
    columns = [('month', 'Mes', 22)] + [(str(n), s['name'][:22], width) for n, s in enumerate(series)]
    rows = []
    for i, month in enumerate(evolution['months']):
        row = {'month': f"{MONTHS_SHORT[month.month - 1]} {month.year}"}
        for n, s in enumerate(series):
            v = s['values'][i]
            row[str(n)] = '—' if v is None else (_money(v) if money else f"{int(v):,}")
        rows.append(row)
    _table(pdf, columns, rows, align_right=tuple(str(n) for n in range(len(series))))


def _distribution(pdf, distribution, fmt):
    _bars(pdf, [(i['name'], f"{_pct(i['percent'])} ({fmt(i['value'])})", i['value']) for i in distribution['items']])


def _pdf_products(pdf, data):
    _heading(pdf, 'Ventas por producto')
    _table(pdf, COLUMNS['products'], _rows('products', data), align_right=('units', 'revenue', 'percent'))
    _heading(pdf, 'Top 5 productos')
    _bars(pdf, [(r['name'], _money(r['revenue']), r['revenue']) for r in data['ranking']])
    _heading(pdf, 'Participación por categoría (productos distintos vendidos)')
    _distribution(pdf, data['distribution'], lambda v: f"{int(v):,}")
    _heading(pdf, 'Evolución de ingresos de los principales productos (últimos 12 meses)')
    _evolution_table(pdf, data['evolution'])
    f = data['featured']
    if f:
        _heading(pdf, 'Producto destacado')
        _paragraph(pdf, f"{f['name']} — Código: {f['code']} — Categoría: {f['category']} — Stock actual: {f['stock']} — "
                        f"Ingresos del período: {_money(f['revenue'])} ({f['units']} unidades)")


def _pdf_customers(pdf, data):
    _heading(pdf, 'Ventas por cliente')
    _table(pdf, COLUMNS['customers'], _rows('customers', data), align_right=('sales', 'units', 'revenue', 'average'))
    _heading(pdf, 'Top clientes')
    _bars(pdf, [(r['name'], _money(r['revenue']), r['revenue']) for r in data['ranking']])
    _heading(pdf, 'Distribución por frecuencia de compra')
    _distribution(pdf, data['distribution'], lambda v: f"{int(v):,} clientes")
    _heading(pdf, 'Evolución de ingresos de los principales clientes (últimos 12 meses)')
    _evolution_table(pdf, data['evolution'])
    _heading(pdf, 'Actividad reciente')
    _table(pdf, [('sale_number', 'Venta', 40), ('customer', 'Cliente', 80), ('when', 'Fecha', 40), ('total', 'Total', 30)],
           [{'sale_number': a['sale_number'], 'customer': a['customer'], 'total': a['total'], 'when': _stamp(a['sold_at'])}
            for a in data['activity']], align_right=('total',))


def _pdf_categories(pdf, data):
    _heading(pdf, 'Detalle por categoría')
    _table(pdf, COLUMNS['categories'], _rows('categories', data), align_right=('products', 'units', 'revenue', 'percent', 'change_percent'))
    _heading(pdf, 'Ventas por categoría')
    _bars(pdf, [(r['name'], _money(r['revenue']), r['revenue']) for r in data['ranking']])
    _heading(pdf, 'Participación por categoría')
    _distribution(pdf, data['distribution'], _money)
    _heading(pdf, 'Evolución por categoría (últimos 12 meses)')
    _evolution_table(pdf, data['evolution'])
    s = data['summary']
    _heading(pdf, 'Resumen')
    _table(pdf, [('k', 'Concepto', 60), ('v', 'Detalle', 130)], [
        {'k': 'Mayor crecimiento', 'v': f"{s['growth']['name']} ({'+' if s['growth']['change_percent'] > 0 else ''}{s['growth']['change_percent']:g}%)" if s['growth'] else 'Sin período anterior comparable'},
        {'k': 'Mayor ticket', 'v': f"{s['ticket']['name']} ({_money(s['ticket']['average_unit'])} promedio por unidad)" if s['ticket'] else '—'},
        {'k': 'Mayor participación', 'v': f"{s['share']['name']} ({_pct(s['share']['percent'])})" if s['share'] else '—'}] if data['ranking'] else [])


def _pdf_payments(pdf, data):
    _heading(pdf, 'Ventas por método de pago')
    _table(pdf, COLUMNS['payments'], _rows('payments', data), align_right=('count', 'revenue', 'average', 'percent'))
    _heading(pdf, 'Top métodos')
    _bars(pdf, [(r['name'], _money(r['revenue']), r['revenue']) for r in data['ranking']])
    _heading(pdf, 'Distribución por método (transacciones)')
    _distribution(pdf, data['distribution'], lambda v: f"{int(v):,}")
    _heading(pdf, 'Evolución por método de pago (transacciones mensuales, últimos 12 meses)')
    _evolution_table(pdf, data['evolution'], money=False)
    s = data['summary']
    _heading(pdf, 'Resumen')
    _table(pdf, [('k', 'Concepto', 60), ('v', 'Detalle', 130)], [
        {'k': 'Mayor participación', 'v': f"{s['share']['name']} ({_pct(s['share']['percent'])})" if s['share'] else '—'},
        {'k': 'Mayor ticket', 'v': f"{s['ticket']['name']} ({_money(s['ticket']['average'])} promedio)" if s['ticket'] else '—'},
        {'k': 'Mayor crecimiento', 'v': f"{s['growth']['name']} ({'+' if s['growth']['change_percent'] > 0 else ''}{s['growth']['change_percent']:g}%)" if s['growth'] else 'Sin período anterior comparable'},
        {'k': 'Método más estable', 'v': s['stable']['name'] if s['stable'] else 'Sin período anterior comparable'}] if data['ranking'] else [])


def _pdf_summary(pdf, data):
    m = data['monthly']
    _heading(pdf, 'Tendencia de ventas (últimos 12 meses)')
    if m['available']:
        _table(pdf, [('month', 'Mes', 50), ('current', 'Ventas actuales', 70), ('previous', 'Año anterior', 70)],
               [{'month': f"{MONTHS_SHORT[int(month[5:7]) - 1]} {month[:4]}", 'current': '—' if c is None else _money(c), 'previous': '—' if p is None else _money(p)}
                for month, c, p in zip(m['months'], m['current'], m['previous_year'])], align_right=('current', 'previous'))
    else:
        _paragraph(pdf, 'Sin ventas registradas todavía.')
    _heading(pdf, 'Ventas por categoría')
    _bars(pdf, [(i['name'], f"{_pct(i['percent'])} ({_money(i['revenue'])})", i['revenue']) for i in data['by_category']['items']])
    _heading(pdf, 'Top productos')
    _table(pdf, [('n', '#', 10), ('name', 'Producto', 100), ('revenue', 'Ingresos', 40), ('units', 'Unidades', 40)],
           [{'n': n, **p} for n, p in enumerate(data['top_products'], 1)], align_right=('revenue', 'units'))
    _heading(pdf, 'Top clientes')
    _table(pdf, [('n', '#', 10), ('name', 'Cliente', 100), ('revenue', 'Ingresos', 40), ('sales', 'Compras', 40)],
           [{'n': n, 'name': c['name'], 'revenue': Decimal(c['revenue']), 'sales': c['sales']} for n, c in enumerate(data['top_customers'], 1)], align_right=('revenue', 'sales'))
    _heading(pdf, 'Resumen ejecutivo')
    _table(pdf, [('k', 'Concepto', 60), ('v', 'Detalle', 130)], [{'k': k, 'v': v} for k, v in _executive(data)] if data['kpis']['count'][0] else [])


_PDF = {'summary': _pdf_summary, 'products': _pdf_products, 'customers': _pdf_customers, 'categories': _pdf_categories, 'payments': _pdf_payments}


def pdf_file(tab: str, data: dict, now: datetime) -> BytesIO:
    pdf = ReportPDF(orientation='P', format='A4')
    pdf.core_fonts_encoding = 'cp1252'
    pdf.set_margins(10, 10, 10)
    pdf.set_auto_page_break(True, 18)
    pdf.add_page()
    pdf.set_font('Helvetica', 'B', 18)
    pdf.cell(0, 10, pdf_text(TITLES[tab]), new_x='LMARGIN', new_y='NEXT')
    _paragraph(pdf, f"Período: {_range(*data['period'])}   |   Comparación: {_range(*data['comparison_period'])}")
    _paragraph(pdf, f"Generado: {now:%d/%m/%Y %H:%M} (America/Caracas)   |   Ventas completadas"
                    + (f"   |   Filtro: {data['q']}" if data['q'] else ''))
    pdf.ln(3)
    _kpi_boxes(pdf, _kpis(tab, data))
    _PDF[tab](pdf, data)
    return BytesIO(bytes(pdf.output()))


def _physical_pdf(tab, data, now):
    pdf = ReportPDF(orientation='P', format='A4')
    pdf.core_fonts_encoding = 'cp1252'
    pdf.set_margins(10, 10, 10)
    pdf.set_auto_page_break(True, 18)
    pdf.add_page()
    _heading(pdf, TITLES[tab])
    _paragraph(pdf, f"Período: {_range(*data['period'])} | Generado: {now:%d/%m/%Y %H:%M} (America/Caracas)")
    _paragraph(pdf, 'Vista física: cantidades y conteos, sin importes.')
    if data['q']:
        _paragraph(pdf, f"Filtro: {data['q']}")
    _kpi_boxes(pdf, [(label, str(value), 'Conteo físico del período') for label, value in data['kpis']])
    _table(pdf, sales_report_service.PHYSICAL_COLUMNS[tab], data['rows'], align_right=('units', 'sales', 'products'))
    return BytesIO(bytes(pdf.output()))


def export(tab, format_name, params: dict, now=None):
    """(buffer, mimetype, filename) del reporte real de la pestaña, con el período/filtro recibidos."""
    if format_name not in ('csv', 'pdf'):
        raise ValidationError('Formato inválido. Seleccione csv o pdf.')
    data = sales_report_service.report(tab, params)
    if len(data['rows']) > MAX_EXPORT_ROWS:
        raise ValidationError(f'La exportación supera {MAX_EXPORT_ROWS} filas. Reduzca el período.')
    now = now or datetime.now(COMMERCIAL_ZONE)
    start, end = (d.isoformat() for d in data['period'])
    tab = data['tab']
    if data.get('physical'):
        columns = sales_report_service.PHYSICAL_COLUMNS[tab]
        output = csv_file(data['rows'], columns, now) if format_name == 'csv' else _physical_pdf(tab, data, now)
    elif format_name == 'csv':
        rows = _summary_rows(data) if tab == 'summary' else _rows(tab, data)
        output = csv_file(rows, COLUMNS[tab], now)
    else:
        output = pdf_file(tab, data, now)
    return output, MIMES[format_name], f"reporte_ventas_{STEMS[tab]}_{start}_{end}.{format_name}"
