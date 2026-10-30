"""Controladores de los dashboards de solo lectura (Inicio, Resumen de Inventario, Ventas).

Los ``*_view`` adaptan el agregado JSON de cada servicio a la forma que ya dibujan las
plantillas del modo de referencia (misma geometría, datos reales): formato de dinero,
fechas comerciales (America/Caracas), insignias y escalas de los gráficos. Sin reglas de negocio.
"""
import math
from datetime import date, datetime, timezone
from decimal import Decimal

from flask import jsonify, request, url_for
from flask_login import current_user

from app.services import dashboard_service, inventory_summary_service, sales_dashboard_service
from app.services.inventory_service import COMMERCIAL_ZONE

MONTHS_ABBR = ("ene.", "feb.", "mar.", "abr.", "may.", "jun.", "jul.", "ago.", "sept.", "oct.", "nov.", "dic.")
MONTHS_SHORT = ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")
NO_COMPARISON = "Sin comparación disponible"
STATUS_BADGE = {"agotado": ("Agotado", "red"), "bajo_stock": ("Bajo stock", "orange"),
                "disponible": ("Disponible", "green"), "inactivo": ("Inactivo", "gray")}
MOVEMENT_ICON = {"entrada": ("svg:download", "green"), "salida": ("svg:upload", "red"),
                 "ajuste": ("ti-adjustments-horizontal", "orange")}
SALE_ICON = ("svg:cart", "blue")
BAR_TONES = ("blue", "green", "orange", "purple", "teal", "gray")


def summary():
    return jsonify(dashboard_service.summary(current_user.role))


def inventory_summary():
    return jsonify(inventory_summary_service.summary())


def sales_dashboard():
    return jsonify(sales_dashboard_service.dashboard(request.args.to_dict()))


# --- Formato compartido -----------------------------------------------------------

def _money(value) -> str:
    return f"$ {Decimal(value):,.2f}"


def _num(value) -> str:
    return f"{int(value):,}"


def _local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).astimezone(COMMERCIAL_ZONE)


def _when(iso: str | None, today: date) -> str:
    """"Hoy, 11:42 a.m." / "Ayer, 04:35 p.m." / "25 may., 09:10 a.m." en hora comercial."""
    if not iso:
        return "—"
    local = _local(iso)
    clock = local.strftime("%I:%M ") + ("a.m." if local.hour < 12 else "p.m.")
    days = (today - local.date()).days
    prefix = "Hoy" if days == 0 else "Ayer" if days == 1 else f"{local.day:02d} {MONTHS_ABBR[local.month - 1]}"
    return f"{prefix}, {clock}"


def _day_label(day: date, year=True) -> str:
    text = f"{day.day:02d} {MONTHS_ABBR[day.month - 1]}"
    return f"{text} {day.year}" if year else text


def _range_label(start: str, end: str) -> str:
    return f"{_day_label(date.fromisoformat(start))} - {_day_label(date.fromisoformat(end))}"


def _scale(values, steps=5):
    """Máximo y paso "redondos" del eje Y para que Chart.js no dibuje una escala vacía."""
    top = max([float(v) for v in values if v is not None] or [0])
    if top <= 0:
        return steps, 1
    magnitude = 10 ** math.floor(math.log10(top / steps))
    for factor in (1, 2, 2.5, 5, 10):
        step = factor * magnitude
        if step * steps >= top:
            return step * steps, step
    return top, top / steps


def _thumb(name, image_url):
    return {"name": name, "src": image_url, "thumb": None}


def _card(icon, tone, label, value, sub, trend=None, trend_tone="gray", arrow=None, solid=False):
    return {"icon": icon, "tone": tone, "solid": solid, "label": label, "value": value,
            "trend": trend, "trend_tone": trend_tone, "arrow": arrow, "sub": sub, "sub_trend": None}


def _delta_trend(comparison, up_is_good, as_percent):
    """(texto, tono, flecha) de una comparación con cierre real; None sin cierre."""
    if not comparison["available"]:
        return None, "gray", None
    delta = comparison["delta"]
    if delta == 0:
        return "0", "gray", None
    text = f"{abs(comparison['change_percent']):g}%" if as_percent and comparison["change_percent"] is not None else str(abs(delta))
    good = (delta > 0) == up_is_good
    return text, "green" if good else "red", "up" if delta > 0 else "down"


# --- Resumen de Inventario ----------------------------------------------------------

def _inventory_kpi(icon, tone, label, kpi, fmt, up_is_good, as_percent, solid=False):
    comparison = kpi["comparison"]
    trend, trend_tone, arrow = _delta_trend(comparison, up_is_good, as_percent)
    sub = f"vs. ayer {fmt(comparison['value'])}" if comparison["available"] else NO_COMPARISON
    return _card(icon, tone, label, fmt(kpi["value"]), sub, trend, trend_tone, arrow, solid)


def _activity_row(item, today):
    sale = item["sale_number"] is not None
    icon, tone = SALE_ICON if sale else MOVEMENT_ICON.get(item["movement_type"], ("svg:cube", "gray"))
    if sale and item["customer_name"]:
        desc = f"Cliente: {item['customer_name']}"
    else:
        desc = f"{item['reference']} · {item['product_name'] or ''}".rstrip(" ·")
    delta = item["delta"]
    return (icon, tone, item["title"], desc, _when(item["created_at"], today),
            f"{'+' if delta >= 0 else '-'} {abs(delta)} uds.", "up" if delta >= 0 else "down")


def inventory_summary_view(data: dict) -> dict:
    today = date.fromisoformat(data["today"])
    k, chart, dist = data["kpis"], data["movement_chart"], data["inventory_distribution"]
    y_max, y_step = _scale(chart["entries"] + chart["exits"])
    rotation = data["top_rotation"]
    cards = [
        _inventory_kpi("svg:cube", "green", "Productos en stock", k["products_in_stock"], _num, True, True),
        _inventory_kpi("svg:alert-triangle", "orange", "Bajo stock", k["low_stock"], _num, False, False),
        _inventory_kpi("svg:cube", "red", "Sin stock", k["out_of_stock"], _num, False, False),
    ]
    money = k.get("inventory_value")
    if money and money["available"]:
        from app.utils.purchase_currency import format_amount
        amounts = " · ".join(format_amount(item["amount"], item["currency"]) for item in money["totals_by_currency"])
        note = NO_COMPARISON
        if money["unknown_currency_products"]:
            note += f" · {money['unknown_currency_products']} productos sin moneda, excluidos"
        cards.append(_card("ti-currency-dollar", "blue", "Valor del inventario", amounts, note, solid=True))
    return {
        "kpis": cards,
        "chart": {
            "title": "Movimientos de inventario en los últimos 7 días", "range": "Últimos 7 días",
            "labels": [_day_label(date.fromisoformat(d), year=False) for d in chart["days"]],
            "series": [{"label": "Entradas (unidades)", "data": chart["entries"]},
                       {"label": "Salidas (unidades)", "data": chart["exits"], "dashed": True}],
            "y_max": y_max, "y_step": y_step,
        },
        "donut": {
            "title": "Inventario por categoría", "center_value": _num(dist["center_value"]),
            "center_label": "Productos totales", "empty": "Sin productos en stock",
            "items": [(i["name"], f"{i['percent']:g}% ({_num(i['value'])})", i["color"], i["value"]) for i in dist["items"]],
        },
        "alerts": {
            "title": "Alertas de inventario", "link": "Ver todas", "href": "/products?status=bajo_stock",
            "empty": "Sin alertas de stock",
            "rows": [(_thumb(a["name"], a["image_url"]), f"Stock actual: {a['current_stock']} unidades",
                      STATUS_BADGE[a["status"]]) for a in data["stock_alerts"]["items"]],
        },
        "activity": {
            "title": "Movimientos recientes", "link": "Ver todos", "href": "/inventory",
            "empty": "Sin movimientos registrados",
            "rows": [_activity_row(m, today) for m in data["recent_movements"]["items"]],
        },
        "low_stock": {
            "title": "Productos con menor stock", "link": "Ver todas", "href": "/products",
            "columns": ["Producto", "Categoría", "Stock actual", "Stock mínimo", "Estado", "Última actualización"],
            "empty": "Sin productos activos",
            "rows": [(_thumb(p["name"], p["image_url"]), p["category"] or "—", str(p["current_stock"]),
                      str(p["minimum_stock"]), STATUS_BADGE[p["status"]], _when(p["updated_at"], today))
                     for p in data["lowest_stock"]["items"]],
        },
        "rotation": {
            "title": "Productos con mayor rotación",
            "columns": ["Producto", f"Salidas ({rotation['window_days']}d)", "Stock actual", "Rotación relativa"],
            "empty": f"Sin ventas completadas en los últimos {rotation['window_days']} días",
            "rows": [(_thumb(p["name"], p["image_url"]), _num(p["units"]), str(p["current_stock"]),
                      STATUS_BADGE[p["status"]][1], p["percent"]) for p in rotation["items"]],
            "total_label": "Total unidades movidas", "total": f"{_num(rotation['total_units'])} uds.",
        },
    }


# --- Dashboard de Ventas ------------------------------------------------------------

def _sales_kpi(icon, tone, label, kpi, fmt, previous_label, solid=False):
    comparison = kpi["comparison"]
    if comparison["reason"] == "ok":
        percent = comparison["change_percent"]
        trend, trend_tone, arrow = f"{abs(percent):g}%", ("green" if percent > 0 else "red" if percent < 0 else "gray"), \
            ("up" if percent > 0 else "down" if percent < 0 else None)
    elif comparison["reason"] == "new":
        trend, trend_tone, arrow = "Nuevo", "green", False
    else:
        trend, trend_tone, arrow = None, "gray", None
    return _card(icon, tone, label, fmt(kpi["value"]), previous_label, trend, trend_tone, arrow, solid)


def _page_items(page, pages):
    numbers = sorted({1, 2, 3, page, pages} & set(range(1, pages + 1))) if pages > 5 else range(1, pages + 1)
    items, last = [], 0
    for n in numbers:
        if n - last > 1:
            items.append({"text": "...", "gap": True})
        items.append({"text": str(n), "active": n == page, "page": n})
        last = n
    return items


def _sale_stamp(iso):
    return _local(iso).strftime("%d/%m/%Y %H:%M") if iso else "—"


def sales_dashboard_view(data: dict) -> dict:
    today = date.fromisoformat(data["today"])
    k, monthly, cat, pay, recent = data["kpis"], data["monthly"], data["by_category"], data["payment_methods"], data["recent_sales"]
    previous = "vs. " + _range_label(data["comparison_period"]["start"], data["comparison_period"]["end"])
    current = [None if v is None else float(v) for v in monthly["current"]]
    series = [{"label": "Ventas ($)", "data": current}]
    if monthly["previous_year_available"]:
        series.append({"label": "Ventas año anterior ($)", "data": [None if v is None else float(v) for v in monthly["previous_year"]], "dashed": True})
    y_max, y_step = _scale([v for s in series for v in s["data"]])
    first, last = recent["per_page"] * (recent["page"] - 1) + 1, min(recent["per_page"] * recent["page"], recent["total"])
    return {
        "period": data["period"], "date_range": _range_label(data["period"]["start"], data["period"]["end"]),
        "compare": "Comparar con: " + _range_label(data["comparison_period"]["start"], data["comparison_period"]["end"]),
        "kpis": [
            _sales_kpi("ti-currency-dollar", "blue", "Ventas del período", k["revenue"], _money, previous, solid=True),
            _sales_kpi("svg:cart", "green", "Ventas realizadas", k["sales_count"], _num, previous),
            _sales_kpi("svg:tag", "purple", "Ticket promedio", k["average_ticket"], _money, previous),
            _sales_kpi("svg:user", "orange", "Clientes nuevos", k["new_customers"], _num, previous),
        ],
        "sales_chart": {
            "title": "Ventas de los últimos 12 meses", "range": "Mensual",
            "labels": [MONTHS_SHORT[date.fromisoformat(m).month - 1] for m in monthly["months"]],
            "series": series, "legend": [(s["label"], bool(s.get("dashed"))) for s in series],
            "y_max": y_max, "y_step": y_step,
            "note": None if monthly["available"] else "Sin ventas registradas todavía",
            "coverage": ("Historial desde " + _day_label(date.fromisoformat(monthly["coverage_from"]))) if monthly["coverage_from"] else None,
        },
        "donut": {
            "title": "Ventas por categoría", "range": "Ingresos", "center_value": f"$ {Decimal(cat['total']):,.0f}",
            "center_label": "Ventas totales", "empty": "Sin ventas en el período",
            "note": "Agrupado por la categoría actual de cada producto",
            "items": [(i["name"], f"{i['percent']:g}% ({_money(i['revenue'])})", i["color"], float(i["revenue"])) for i in cat["items"]],
        },
        "payments": {
            "title": "Métodos de pago", "range": "Período", "empty": "Sin ventas en el período",
            "bars": {"values": [float(i["revenue"]) for i in pay["items"]],
                     "value_labels": [f"$ {Decimal(i['revenue']):,.0f}" for i in pay["items"]],
                     "tones": [BAR_TONES[n % len(BAR_TONES)] for n in range(len(pay["items"]))],
                     "x_labels": [[i["label"], f"{i['percent']:g}%"] for i in pay["items"]],
                     **dict(zip(("y_max", "y_step"), _scale([i["revenue"] for i in pay["items"]])))},
        },
        "latest": {
            "title": "Últimas ventas registradas", "columns": ["#", "Cliente", "Fecha", "Total", "Estado"],
            "empty": "Sin ventas registradas",
            "rows": [(SALE_ICON[0], SALE_ICON[1], f"#{s['sale_number']}", s["customer"] or "—", _when(s["sold_at"], today),
                      _money(s["total"]), (s["status_label"], "green")) for s in data["latest_sales"]["items"]],
        },
        "recent": {
            "title": "Ventas recientes", "link": "Ver todas",
            "href": url_for("pages.sales_list", date_from=data["period"]["start"], date_to=data["period"]["end"]),
            "columns": ["#", "Fecha", "Cliente", "Productos", "Método de pago", "Estado", "Total"],
            "empty": "Sin ventas en el período",
            "rows": [(first + n, _sale_stamp(s["sold_at"]), s["customer"] or "—", f"{s['products']} productos",
                      s["payment_method_label"], (s["status_label"], "green"), _money(s["total"]))
                     for n, s in enumerate(recent["items"])],
            "pagination": {"summary": (f"Mostrando {first} a {last} de {recent['total']} ventas" if recent["total"]
                                       else "Sin ventas en el período"),
                           "page": recent["page"], "pages": recent["pages"], "items": _page_items(recent["page"], recent["pages"])},
        },
        "customers": {
            "title": "Clientes destacados", "columns": ["#", "Cliente", "Ventas ($)", "N.º ventas"],
            "empty": "Sin ventas en el período",
            "rows": [(n + 1, c["name"], _money(c["revenue"]), str(c["sales"])) for n, c in enumerate(data["top_customers"]["items"])],
        },
    }
