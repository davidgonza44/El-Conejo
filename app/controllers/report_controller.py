"""Controlador de reportes: lee query params y delega en report_service.

``sales_report_view`` adapta el agregado real de ``sales_report_service`` a la MISMA forma
que dibuja la plantilla del modo de referencia (report_sales_reference.reference_view):
dinero, fechas comerciales, insignias, paginación y escalas de los gráficos. Sin reglas de negocio.
"""
import math
from datetime import date
from decimal import Decimal

from flask import jsonify, request, url_for

from app.controllers import attachment_response
from app.controllers.dashboard_controller import (MONTHS_SHORT, _num, _page_items, _range_label,
                                                  _scale, _when)
from app.models.category import CATEGORY_COLORS
from app.services import report_service, sales_report_export_service, sales_report_service
from app.services.exceptions import ValidationError

PER_PAGE = 8
PER_PAGE_CHOICES = (8, 20, 50)
CATEGORY_ICON = {'tag': 'tag', 'package': 'boxes', 'zap': 'bolt', 'wrench': 'wrench', 'hammer': 'hammer',
                 'paintbrush': 'tag', 'droplet': 'tag', 'shield': 'shield', 'hard-hat': 'helmet'}
CATEGORY_TONE = {'blue': 'blue', 'green': 'green', 'orange': 'orange', 'purple': 'purple', 'teal': 'teal',
                 'red': 'red', 'pink': 'purple', 'yellow': 'orange', 'gray': 'muted'}
PAYMENT_ICON = {'transfer': ('bank', 'muted'), 'cash': ('cash', 'green'), 'credit_card': ('card', 'blue'),
                'debit_card': ('card', 'blue'), 'check': ('file', 'muted'), 'receivable': ('clock', 'orange')}
STOCK_STATUS = {'agotado': ('Agotado', 'red'), 'bajo_stock': ('Bajo stock', 'orange'), 'inactivo': ('Inactivo', 'muted')}
EMPTY = 'Sin ventas completadas en el período'


def _money(value) -> str:
    """Sin símbolo $: las ventas no registran moneda de origen (sales_report_service.CURRENCY_NOTE)."""
    return f"{Decimal(value):,.2f}"


def _dates() -> dict:
    return {
        "date_from": request.args.get("date_from"),
        "date_to": request.args.get("date_to"),
    }


def dashboard_summary():
    return jsonify(report_service.dashboard_summary())


def stock_vs_minimum():
    return jsonify(report_service.stock_vs_minimum())


def low_stock_products():
    return jsonify(report_service.low_stock_products())


def products_without_movement():
    return jsonify(
        report_service.products_without_movement(days=request.args.get("days"))
    )


def excess_stock_products():
    return jsonify(
        report_service.excess_stock_products(multiplier=request.args.get("multiplier"))
    )


def entries_vs_exits():
    return jsonify(report_service.entries_vs_exits(**_dates()))


def movements_by_category():
    return jsonify(report_service.movements_by_category(**_dates()))


def top_products_by_exits():
    return jsonify(
        report_service.top_products_by_exits(**_dates(), limit=request.args.get("limit"))
    )


def least_products_by_exits():
    return jsonify(
        report_service.least_products_by_exits(**_dates(), limit=request.args.get("limit"))
    )


def inventory_adjustments():
    return jsonify(report_service.inventory_adjustments(**_dates()))


def delivery_notes_by_period():
    return jsonify(report_service.delivery_notes_by_period(**_dates()))


def top_delivered_products():
    return jsonify(
        report_service.top_delivered_products(**_dates(), limit=request.args.get("limit"))
    )


def delivery_notes_by_user():
    return jsonify(report_service.delivery_notes_by_user(**_dates()))


def delivery_notes_by_customer():
    return jsonify(
        report_service.delivery_notes_by_customer(
            **_dates(), limit=request.args.get("limit")
        )
    )


# --- Reportes de ventas (página y exportación) ------------------------------------------

def export_sales_report():
    params = request.args.to_dict()
    output, mime, filename = sales_report_export_service.export(params.pop('tab', None), params.pop('format', None), params)
    return attachment_response(output, mime, filename)


def _pct(value) -> str:
    return f"{value:g}%" if value is not None else "—"


def _trend(comparison) -> tuple:
    """(texto, tono, flecha) de una comparación real; sin cierre -> None."""
    if not comparison:
        return None, "muted", None
    if comparison["reason"] == "ok":
        percent = comparison["change_percent"]
        return _pct(abs(percent)), ("green" if percent > 0 else "red" if percent < 0 else "muted"), ("up" if percent > 0 else "down" if percent < 0 else None)
    if comparison["reason"] == "new":
        return "Nuevo", "green", None
    return None, "muted", None


def _kpi(icon, tone, label, value, comparison, sub, trend=None):
    text, trend_tone, arrow = _trend(comparison) if trend is None else trend
    return {"icon": icon, "tone": tone, "label": label, "value": value, "trend": text, "trend_tone": trend_tone, "arrow": arrow, "sub": sub}


def _thumb(row) -> dict:
    return {"kind": "product", "name": row["name"], "src": row.get("image_url"), "thumb": None}


def _delta(value) -> dict:
    return {"kind": "delta", "text": _pct(abs(value)) if value is not None else "—",
            "tone": "muted" if value is None or value == 0 else "green" if value > 0 else "red", "arrow": None if value is None or value == 0 else "up" if value > 0 else "down"}


def _category_cell(row) -> dict:
    tone = CATEGORY_TONE.get(row.get("color") if row.get("color") in CATEGORY_COLORS else "blue", "blue")
    return {"kind": "method", "text": row["name"], "icon": CATEGORY_ICON.get(row.get("icon"), "tag"), "tone": tone}


def _payment_cell(row) -> dict:
    icon, tone = PAYMENT_ICON.get(row["method"], ("card", "muted"))
    return {"kind": "method", "text": row["name"], "icon": icon, "tone": tone}


def _action(href) -> dict:
    return {"kind": "action", "text": "Ver detalle", "href": href}


def _paginate(rows, params, noun, base):
    page = params.get("page", 1)
    per_page = params.get("per_page", PER_PAGE)
    try:
        page, per_page = int(page), int(per_page)
    except (TypeError, ValueError):
        raise ValidationError("Los parámetros 'page' y 'per_page' deben ser enteros.")
    if per_page not in PER_PAGE_CHOICES or page < 1:
        raise ValidationError(f"per_page debe ser uno de {', '.join(map(str, PER_PAGE_CHOICES))} y page >= 1.")
    total = len(rows)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    first, last = (page - 1) * per_page + 1 if total else 0, min(page * per_page, total)
    items = [{**item, "href": base + str(item["page"])} if not item.get("gap") else item for item in _page_items(page, pages)]
    return rows[first - 1:last] if total else [], first, {
        "summary": f"Mostrando {first} a {last} de {total} {noun}" if total else f"Sin {noun} en el período",
        "page": page, "pages": pages, "items": items, "per_page": per_page, "choices": PER_PAGE_CHOICES,
        "prev": base + str(page - 1) if page > 1 else None, "next": base + str(page + 1) if page < pages else None,
    }


def _line_chart(evolution, money=True) -> dict:
    series = [{"label": s["name"], "data": [None if v is None else float(v) for v in s["values"]]} for s in evolution["series"]]
    y_max, y_step = _scale([v for s in series for v in s["data"]])
    if not money:  # transacciones: pasos enteros, nunca 2.5
        y_step = math.ceil(y_step)
        y_max = y_step * 5
    return {"labels": [MONTHS_SHORT[m.month - 1] for m in evolution["months"]], "series": series,
            "y_max": y_max, "y_step": y_step, "money": money, "empty": None if series else EMPTY}


def _donut(title, distribution, center_value, center_label, fmt) -> dict:
    items = distribution["items"]
    tones = [6 if item["id"] is None and item["name"] == "Otros" else n + 1 for n, item in enumerate(items)]
    return {"title": title, "center_value": center_value, "center_label": center_label, "empty": EMPTY,
            "values": [float(item["percent"]) for item in items], "tones": tones,
            "items": [{"name": item["name"], "sub": f"{_pct(item['percent'])} ({fmt(item['value'])})", "tone": tone} for item, tone in zip(items, tones)]}


def _ranking(title, rows, name_cell, amount_key="revenue") -> dict:
    top = max([Decimal(r[amount_key]) for r in rows] or [0])
    return {"title": title, "empty": EMPTY, "items": [
        {"name": name_cell(r), "amount": _money(r[amount_key]), "width": round(float(Decimal(r[amount_key]) * 100 / top), 1) if top else 0} for r in rows]}


def _history_link(view, kind="all", key=None, filtered=True) -> str:
    filters = {"tab": view["tab"], "start": view["period"]["start"],
               "end": view["period"]["end"], "q": view["q"] if filtered else ""}
    if kind == "all":
        return url_for("pages.report_sales_history", **filters)
    return url_for("pages.report_sales_history_detail", kind=kind, key=key, **filters)


def _common(data: dict, params: dict) -> dict:
    start, end = (d.isoformat() for d in data["period"])
    p_start, p_end = (d.isoformat() for d in data["comparison_period"])
    previous = "vs. " + _range_label(p_start, p_end)
    query = {"tab": data["tab"], "start": start, "end": end, **({"q": data["q"]} if data["q"] else {}),
             **({"category": params["category"]} if data.get("category") else {})}
    if params.get("per_page") and params["per_page"] != str(PER_PAGE):
        query["per_page"] = params["per_page"]
    return {"tab": data["tab"], "tabs": sales_report_service.TABS, "reference": False, "q": data["q"],
            "category": data.get("category"), "category_id": params.get("category") if data.get("category") else None,
            "notes": [sales_report_service.CURRENCY_NOTE] + (["Agrupación según la categoría actual de cada producto: las ventas anteriores no conservan la categoría del momento."]
                                        if data["tab"] in ("categories", "products", "summary") else []),
            "period": {"start": start, "end": end}, "today": data["today"].isoformat(),
            "date_range": _range_label(start, end), "compare": "Comparar con: " + _range_label(p_start, p_end),
            "previous": previous, "page_base": url_for("pages.report_sales", **query) + "&page=",
            "tab_href": {key: url_for("pages.report_sales", tab=key, start=start, end=end) for key, _ in sales_report_service.TABS}}


def _products_view(data, view, params) -> dict:
    k = data["kpis"]
    view["kpis"] = [
        _kpi("boxes", "blue", "Productos vendidos", _num(k["products"][0]), k["products"][1], view["previous"]),
        _kpi("cart", "green", "Unidades vendidas", f"{_num(k['units'][0])} uds.", k["units"][1], view["previous"]),
        _kpi("dollar", "blue", "Ingresos por producto", _money(k["revenue"][0]), k["revenue"][1], view["previous"]),
        _kpi("tag", "purple", "Ingreso prom. por producto", _money(k["average"][0]), k["average"][1], view["previous"]),
    ]
    rows, first, pagination = _paginate(data["rows"], params, "productos", view["page_base"])
    view["table"] = {"title": "Ventas por producto", "link": "Ver todo", "href": _history_link(view), "empty": EMPTY,
                     "columns": ["#", "Producto", "Código", "Categoría", "Unidades", "Ingresos", "Participación", "Acciones"],
                     "widths": [4, 25, 10, 18, 9, 11, 12, 12], "pagination": pagination,
                     "rows": [[str(first + n), _thumb(r), {"kind": "muted", "text": r["code"]}, {"kind": "muted", "text": r["category"]},
                               _num(r["units"]), _money(r["revenue"]), _pct(r["percent"]), _action(_history_link(view, "products", r["id"]))]
                              for n, r in enumerate(rows)]}
    view["ranking"] = _ranking("Top 5 productos", data["ranking"], _thumb)
    view["donut"] = _donut("Participación por categoría", data["distribution"], _num(k["products"][0]), "Productos", _num)
    view["chart"] = {"title": "Evolución de ventas por producto", "subtitle": "Ingresos mensuales de los principales productos (últimos 12 meses)",
                     **_line_chart(data["evolution"])}
    featured = data["featured"]
    status = "inactivo" if featured and not featured["is_active"] else "agotado" if featured and featured["stock"] == 0 else "bajo_stock" if featured and featured["stock"] <= featured["minimum_stock"] else "disponible"
    view["featured"] = {"title": "Producto destacado", "empty": EMPTY, "product": _thumb(featured) if featured else None,
                        "code": f"Código: {featured['code']}" if featured else "", "category": f"Categoría: {featured['category']}" if featured else "",
                        "stock": f"Stock: {featured['stock']}" if featured else "", **dict(zip(("status", "status_tone"), STOCK_STATUS.get(status, ("Disponible", "green"))))}
    return view


def _customers_view(data, view, params) -> dict:
    k, today = data["kpis"], date.fromisoformat(view["today"])
    view["kpis"] = [
        _kpi("users", "blue", "Clientes identificados", _num(k["active"][0]), k["active"][1], view["previous"]),
        _kpi("refresh", "green", "Clientes recurrentes", _pct(k["recurring"][0]), k["recurring"][1], view["previous"]),
        _kpi("dollar", "blue", "Ingresos por cliente", _money(k["revenue"][0]), k["revenue"][1], view["previous"]),
        _kpi("tag", "purple", "Ticket promedio", _money(k["average"][0]), k["average"][1], view["previous"]),
    ]
    rows, first, pagination = _paginate(data["rows"], params, "clientes", view["page_base"])
    view["table"] = {"title": "Ventas por cliente", "link": "Ver todo", "href": _history_link(view), "empty": EMPTY,
                     "columns": ["#", "Cliente", "RIF", "Compras", "Productos", "Total", "Ticket prom.", "Estado", "Acciones"],
                     "widths": [4, 19, 12, 9, 9, 12, 12, 11, 12], "pagination": pagination,
                     "rows": [[str(first + n), {"kind": "clip", "text": r["name"]}, {"kind": "muted", "text": r["tax_id"] or "—"}, _num(r["sales"]), _num(r["units"]),
                               _money(r["revenue"]), _money(r["average"]),
                               {"kind": "badge", "text": "Activo" if r["is_active"] else "Inactivo", "tone": "green" if r["is_active"] else "red"},
                               _action(_history_link(view, "customers", r["id"]))] for n, r in enumerate(rows)]}
    view["ranking"] = _ranking("Top clientes", data["ranking"], lambda r: r["name"])
    view["donut"] = _donut("Distribución por frecuencia de compra", data["distribution"], _num(k["active"][0]), "Clientes", _num)
    view["chart"] = {"title": "Evolución de ventas por cliente", "subtitle": "Ingresos mensuales de los principales clientes (últimos 12 meses)",
                     **_line_chart(data["evolution"])}
    view["activity"] = {"title": "Actividad reciente de clientes", "link": "Ver todas", "href": _history_link(view, filtered=False), "empty": EMPTY,
                        "rows": [("cart", "green", f"Venta {a['sale_number']}", a["customer"],
                                  _when(a["sold_at"].isoformat(), today), "+ " + _money(a["total"]), "green")
                                 for a in data["activity"]]}
    return view


def _categories_view(data, view, params) -> dict:
    k = data["kpis"]
    leader, growth = k["leader"][0], k["growth"][0]
    view["kpis"] = [
        _kpi("layers", "blue", "Categorías activas", _num(k["active"][0]), k["active"][1], view["previous"]),
        _kpi("dollar", "green", "Ingresos por categoría", _money(k["revenue"][0]), k["revenue"][1], view["previous"]),
        _kpi("trophy", "orange", "Categoría líder", leader["name"] if leader else "—", None,
             f"{_pct(leader['percent'])} del total" if leader else EMPTY, trend=_trend({"reason": "ok", "change_percent": leader["change_percent"]}) if leader and leader["change_percent"] is not None else (None, "muted", None)),
        _kpi("bars", "purple", "Crecimiento prom.", (f"{'+' if growth > 0 else ''}{growth:g}%" if growth is not None else "—"), None,
             f"Promedio de {k['growth_basis']} categorías con período anterior" if growth is not None else "Sin período anterior comparable", trend=(None, "muted", None)),
    ]
    rows, first, pagination = _paginate(data["rows"], params, "categorías", view["page_base"])
    view["table"] = {"title": "Detalle por categoría", "link": None, "href": None, "empty": EMPTY,
                     "columns": ["#", "Categoría", "Productos vendidos", "Unidades", "Ingresos", "Participación", "Variación", "Acciones"],
                     "widths": [5, 23, 14, 9, 13, 12, 12, 14], "pagination": pagination,
                     "rows": [[str(first + n), _category_cell(r), _num(r["products"]), _num(r["units"]), _money(r["revenue"]), _pct(r["percent"]), _delta(r["change_percent"]),
                               _action(url_for("pages.report_sales", tab="products", category=r["id"], start=view["period"]["start"], end=view["period"]["end"]))]
                              for n, r in enumerate(rows)]}
    labels = [r["name"] for r in data["ranking"]]
    y_max, y_step = _scale([r["revenue"] for r in data["ranking"]])
    view["bars"] = {"title": "Ventas por categoría", "empty": EMPTY, "labels": labels, "values": [float(r["revenue"]) for r in data["ranking"]],
                    "value_labels": [f"{Decimal(r['revenue']):,.0f}" for r in data["ranking"]], "y_max": y_max, "y_step": y_step}
    view["donut"] = _donut("Participación por categoría", data["distribution"], _num(k["active"][0]), "Categorías", _money)
    view["ranking"] = _ranking("Top categorías", data["ranking"], lambda r: r["name"])
    view["chart"] = {"title": "Evolución por categoría", "subtitle": None, **_line_chart(data["evolution"])}
    s = data["summary"]
    view["summary"] = {"title": "Resumen", "icon": "bulb", "empty": EMPTY, "items": [
        ("chart", "green", "Mayor crecimiento:", f"{s['growth']['name']} (+{s['growth']['change_percent']:g}%)" if s["growth"] and s["growth"]["change_percent"] > 0 else "Sin período anterior comparable" if not s["growth"] else f"{s['growth']['name']} ({s['growth']['change_percent']:g}%)"),
        ("bars", "blue", "Mayor ticket:", f"{s['ticket']['name']} ({_money(s['ticket']['average_unit'])} promedio por unidad)" if s["ticket"] else "—"),
        ("trophy", "orange", "Mayor participación:", f"{s['share']['name']} ({_pct(s['share']['percent'])})" if s["share"] else "—"),
    ] if data["ranking"] else []}
    return view


def _payments_view(data, view, params) -> dict:
    k = data["kpis"]
    main = k["main"][0]
    view["kpis"] = [
        _kpi("card", "blue", "Métodos activos", _num(k["active"][0]), k["active"][1], f"de {k['configured']} configurados para ventas"),
        _kpi("cash", "green", "Ventas registradas", _money(k["revenue"][0]), k["revenue"][1], view["previous"]),
        _kpi("transfer", "purple", "Método principal", main["name"] if main else "—", None, f"{_pct(main['percent'])} del total" if main else EMPTY,
             trend=_trend({"reason": "ok", "change_percent": main["change_percent"]}) if main and main["change_percent"] is not None else (None, "muted", None)),
        _kpi("card", "orange", "Ticket promedio por venta", _money(k["average"][0] or 0), k["average"][1], view["previous"]),
    ]
    rows, first, pagination = _paginate(data["rows"], params, "métodos", view["page_base"])
    view["table"] = {"title": "Ventas por método de pago", "link": "Ver todo", "href": _history_link(view), "empty": EMPTY,
                     "columns": ["#", "Método", "Transacciones", "Monto total", "Ticket prom.", "Participación", "Acciones"],
                     "widths": [6, 18, 14, 16, 15, 16, 16], "pagination": pagination,
                     "rows": [[str(first + n), _payment_cell(r), _num(r["count"]), _money(r["revenue"]), _money(r["average"]), _pct(r["percent"]),
                               _action(_history_link(view, "payments", r["method"]))] for n, r in enumerate(rows)]}
    view["ranking"] = _ranking("Top métodos", data["ranking"], _payment_cell)
    view["donut"] = _donut("Distribución por método", data["distribution"], _num(data["distribution"]["total"]), "Transacciones", _num)
    view["chart"] = {"title": "Evolución por método de pago", "subtitle": "Número de transacciones mensuales por método de pago (últimos 12 meses)",
                     **_line_chart(data["evolution"], money=False)}
    s = data["summary"]
    view["summary"] = {"title": "Resumen", "icon": None, "empty": EMPTY, "items": [
        ("trophy", "orange", "Mayor participación:", f"{s['share']['name']} ({_pct(s['share']['percent'])})" if s["share"] else "—"),
        ("bars", "blue", "Mayor ticket:", f"{s['ticket']['name']} ({_money(s['ticket']['average'])} promedio)" if s["ticket"] else "—"),
        ("chart", "green", "Mayor crecimiento:", f"{s['growth']['name']} ({'+' if s['growth']['change_percent'] > 0 else ''}{s['growth']['change_percent']:g}%)" if s["growth"] else "Sin período anterior comparable"),
        ("shield", "blue", "Método más estable:", s["stable"]["name"] if s["stable"] else "Sin período anterior comparable"),
    ] if data["ranking"] else []}
    return view


def _summary_view(data, view, params) -> dict:
    k, m, cat = data["kpis"], data["monthly"], data["by_category"]
    view["kpis"] = [
        _kpi("dollar", "blue", "Ventas del período", _money(k["revenue"][0]), k["revenue"][1], view["previous"]),
        _kpi("cart", "green", "Ventas realizadas", _num(k["count"][0]), k["count"][1], view["previous"]),
        _kpi("tag", "purple", "Ticket promedio", _money(k["average"][0]), k["average"][1], view["previous"]),
        _kpi("users", "blue", "Clientes identificados", _num(k["customers"][0]), k["customers"][1], view["previous"]),
    ]
    series = [{"label": "Ventas actuales", "data": [None if v is None else float(v) for v in m["current"]], "fill": True}]
    if m["previous_year_available"]:
        series.append({"label": "Año anterior", "data": [None if v is None else float(v) for v in m["previous_year"]], "dashed": True})
    y_max, y_step = _scale([v for s in series for v in s["data"]])
    view["chart"] = {"title": "Tendencia de ventas", "subtitle": None, "labels": [MONTHS_SHORT[date.fromisoformat(x).month - 1] for x in m["months"]],
                     "series": series, "y_max": y_max, "y_step": y_step, "money": True, "empty": None if m["available"] else "Sin ventas registradas todavía"}
    tones = [6 if i["id"] is None else n + 1 for n, i in enumerate(cat["items"])]
    view["donut"] = {"title": "Ventas por categoría", "center_value": f"{Decimal(cat['total']):,.0f}", "center_label": "Ventas registradas", "empty": EMPTY,
                     "values": [float(i["percent"]) for i in cat["items"]], "tones": tones,
                     "items": [{"name": i["name"], "sub": f"{_pct(i['percent'])} ({_money(i['revenue'])})", "tone": t} for i, t in zip(cat["items"], tones)]}
    view["top_products"] = {"title": "Top productos", "link": "Ver todo", "href": view["tab_href"]["products"], "empty": EMPTY,
                            "columns": ["#", "Producto", "Ingresos", "Unidades"],
                            "rows": [[str(n + 1), _thumb(r), _money(r["revenue"]), _num(r["units"])] for n, r in enumerate(data["top_products"])]}
    view["top_customers"] = {"title": "Top clientes", "link": "Ver todos", "href": view["tab_href"]["customers"], "empty": EMPTY,
                             "columns": ["#", "Cliente", "Ingresos"],
                             "rows": [[str(n + 1), {"kind": "customer", "text": c["name"], "tone": n + 1 if n < 4 else 6}, _money(c["revenue"])]
                                      for n, c in enumerate(data["top_customers"])]}
    e = data["executive"]
    growth_label = {"revenue": "Ventas del período", "count": "Ventas realizadas", "average": "Ticket promedio", "customers": "Clientes identificados"}
    view["executive"] = {"title": "Resumen ejecutivo", "empty": EMPTY, "items": [
        ("trophy", "orange", "Mejor categoría:", e["category"]["name"] if e["category"] else "—"),
        ("chart", "green", "Mayor crecimiento:", f"{growth_label[e['growth'][0]]} ({'+' if e['growth'][1] > 0 else ''}{e['growth'][1]:g}%)" if e["growth"] else "Sin período anterior comparable"),
        ("bulb", "blue", "Mayor ticket:", e["ticket"]["name"] if e["ticket"] else "—"),
        ("boxes", "orange", "Mayor producto:", e["product"]["name"] if e["product"] else "—"),
        ("card", "blue", "Método principal:", f"{e['payment']['name']} ({_num(e['payment']['count'])} ventas)" if e["payment"] else "—"),
    ] if k["count"][0] else []}
    return view


_VIEWS = {"summary": _summary_view, "products": _products_view, "customers": _customers_view,
          "categories": _categories_view, "payments": _payments_view}


def _physical_view(data, view, params):
    columns = sales_report_service.PHYSICAL_COLUMNS[data['tab']]
    rows, _, pagination = _paginate(data['rows'], params, 'registros', view['page_base'])
    view['physical'] = True
    view['kpis'] = [_kpi('boxes', 'blue', label, _num(value), None, 'Conteo físico del período')
                    for label, value in data['kpis']]
    view['table'] = {'title': dict(sales_report_service.TABS)[data['tab']], 'link': None, 'href': None,
                     'empty': EMPTY, 'columns': [label for _, label, _ in columns],
                     'widths': [round(width * 100 / 190, 2) for _, _, width in columns], 'pagination': pagination,
                     'rows': [[_num(row[key]) if key in ('units', 'sales', 'products') else str(row[key] or '—')
                               for key, _, _ in columns] for row in rows]}
    return view


def sales_report_view(params: dict) -> dict:
    """Vista real de una pestaña: agregado del servicio + formato/paginación de presentación."""
    data = sales_report_service.report(params.get("tab", "summary"), params)
    if data.get('physical'):
        return _physical_view(data, _common(data, params), params)
    return _VIEWS[data["tab"]](data, _common(data, params), params)
