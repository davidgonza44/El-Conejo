"""Controlador LIVE del módulo Análisis predictivo.

Parsea filtros/paginación, invoca ``predictive_analysis_service`` y arma las vistas que
consumen las plantillas de ``templates/predictive`` (misma geometría que la referencia,
datos reales o estados honestos). También expone la exportación CSV de los resultados
filtrados y la creación de una orden de compra desde recomendaciones seleccionadas, que
requiere confirmación humana y delega en ``purchase_service`` con permisos de Compras.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import wraps

from flask import request, send_file, url_for
from flask_login import current_user
from flask_wtf.csrf import ValidationError as CsrfValidationError, validate_csrf

from app.controllers import predictive_presenters as fmt
from app.services import critical_product_service as risk_service, forecast_service, predictive_analysis_service
from app.services import predictive_data_service as data, predictive_metrics_service as metrics
from app.services import replenishment_service, trend_service
from app.services.exceptions import ValidationError
from app.services.export_files import csv_file
from app.services.inventory_service import COMMERCIAL_ZONE
from app.utils.permissions import REPLENISHMENT_CREATE, role_has_permission

PER_PAGE_OPTIONS = (10, 25, 50)
DEFAULT_PER_PAGE = 10
EXPORT_PER_PAGE = 10_000  # solo para la exportación CSV; nunca desde la URL de la página
TREND_PERIODS = (30, 60, 90)
METHOD_OPTIONS = [(forecast_service.AUTO_MODEL, "Automático (mejor backtest)")] + \
                 [(key, label) for key, label in forecast_service.MODEL_LABELS.items()]
RISK_OPTIONS = [("", "Todos")] + [(level, fmt.RISK_UI_LABELS[level]) for level in risk_service.RISK_ORDER]
STATUS_OPTIONS = [("", "Todos"), ("out_of_stock", "Sin stock"), ("low_stock", "Bajo stock"),
                  ("high_rotation", "Rotación alta")]
TREND_TABS = [("up", "Tendencia al alza"), ("down", "Tendencia a la baja"), ("stable", "Tendencia estable"), ("all", "Todas")]
ACCURACY_TABS = [("summary", "Resumen de precisión"), ("category", "Precisión por categoría"),
                 ("product", "Precisión por producto")]
FORECAST_MESSAGE = "Historial insuficiente para generar un pronóstico confiable."
REPLENISHMENT_MESSAGE = "Las recomendaciones predictivas estarán disponibles cuando exista un pronóstico válido."
ACCURACY_MESSAGE = "No existen predicciones históricas evaluables."
TRENDS_MESSAGE = "Sin historial suficiente."


@dataclass
class Params:
    search: str = ""
    category_id: int | None = None
    supplier_id: int | None = None
    horizon: int = forecast_service.DEFAULT_HORIZON_DAYS
    method: str = forecast_service.AUTO_MODEL
    risk: str = ""
    status: str = ""
    trend: str = "up"
    period: int = 30
    tab: str = "summary"
    page: int = 1
    per_page: int = DEFAULT_PER_PAGE

    def query(self, **overrides) -> dict:
        values = {"search": self.search or None, "category": self.category_id, "supplier": self.supplier_id,
                  "horizon": self.horizon if self.horizon != forecast_service.DEFAULT_HORIZON_DAYS else None,
                  "method": self.method if self.method != forecast_service.AUTO_MODEL else None,
                  "risk": self.risk or None, "status": self.status or None,
                  "trend": self.trend if self.trend != "up" else None, "period": self.period if self.period != 30 else None,
                  "tab": self.tab if self.tab != "summary" else None,
                  "per_page": self.per_page if self.per_page != DEFAULT_PER_PAGE else None}
        values.update(overrides)
        return {k: v for k, v in values.items() if v not in (None, "")}


def _int(value, allowed=None, default=None):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if allowed is not None and number not in allowed:
        return default
    return number


def parse_params(args, export: bool = False) -> Params:
    p = Params()
    p.search = (args.get("search") or "").strip()[:100]
    p.category_id = _int(args.get("category"))
    p.supplier_id = _int(args.get("supplier"))
    p.horizon = _int(args.get("horizon"), forecast_service.HORIZONS_DAYS, p.horizon)
    method = args.get("method") or p.method
    p.method = method if method == forecast_service.AUTO_MODEL or method in forecast_service.MODELS else p.method
    p.risk = args.get("risk") if args.get("risk") in risk_service.RISK_ORDER else ""
    p.status = args.get("status") if args.get("status") in dict(STATUS_OPTIONS) else ""
    p.trend = args.get("trend") if args.get("trend") in dict(TREND_TABS) else "up"
    p.period = _int(args.get("period"), TREND_PERIODS, 30)
    p.tab = args.get("tab") if args.get("tab") in dict(ACCURACY_TABS) else "summary"
    p.page = max(1, _int(args.get("page"), default=1) or 1)
    p.per_page = EXPORT_PER_PAGE if export else _int(args.get("per_page"), PER_PAGE_OPTIONS, DEFAULT_PER_PAGE)
    return p


def _matches(item, p: Params) -> bool:
    if p.category_id and item.product.category_id != p.category_id:
        return False
    if p.supplier_id and p.supplier_id not in item.supplier_ids:
        return False
    if p.search:
        term = p.search.lower()
        if term not in item.product.name.lower() and term not in item.product.code.lower():
            return False
    return True


def _paginate(rows: list, p: Params, endpoint: str, noun: str) -> tuple[list, dict]:
    total = len(rows)
    pages = max(1, -(-total // p.per_page))
    page = min(p.page, pages)
    start = (page - 1) * p.per_page
    chunk = rows[start:start + p.per_page]

    def href(n):
        return url_for(endpoint, **p.query(page=n if n != 1 else None))

    items = []
    for n in range(1, pages + 1):
        if n == 1 or n == pages or abs(n - page) <= 2:
            items.append({"text": str(n), "active": n == page, "href": href(n)})
        elif items and not items[-1].get("gap"):
            items.append({"text": "...", "gap": True})
    summary = (f"Mostrando {start + 1 if total else 0} a {start + len(chunk)} de {total:,} {noun}")
    return chunk, {"summary": summary, "pages": items if pages > 1 else [], "per_page": f"{p.per_page} por página",
                   "prev": href(page - 1) if page > 1 else None, "next": href(page + 1) if page < pages else None,
                   "per_page_options": [(n, f"{n} por página", url_for(endpoint, **p.query(per_page=n, page=None)))
                                        for n in PER_PAGE_OPTIONS], "total": total}


def _select(label, name, options, value):
    return {"label": label, "name": name, "options": options, "value": "" if value is None else str(value)}


def _categories(analysis):
    return [("", "Todas")] + [(str(c.id), c.name) for c in analysis.categories]


def _suppliers(analysis):
    return [("", "Todos")] + [(str(s.id), s.name) for s in analysis.suppliers]


def _horizons():
    return [(str(h), f"{h} días") for h in forecast_service.HORIZONS_DAYS]


def _state(available: bool, title: str, message: str, hints: list[str] | None = None) -> dict:
    return {"available": available, "title": title, "message": message, "hints": hints or []}


def _hints(analysis) -> list[str]:
    breakdown = fmt.eligibility_breakdown(analysis)
    hints = [f"{n} producto(s): {label}" for label, n in breakdown]
    hints.append("Fuente de demanda: históricos confirmados + ventas completadas + notas emitidas.")
    return hints


def _common(analysis, p: Params, endpoint: str) -> dict:
    return {"live": True, "params": p, "export_url": url_for("predictions.export", page=endpoint.split(".")[-1], **p.query()),
            "refresh_url": url_for(endpoint, **p.query()), "form_action": url_for(endpoint),
            "generated_at": analysis.generated_at.strftime("%d/%m/%Y %H:%M"),
            "computation_status": analysis.computation_status,
            "computation_status_url": _status_url(analysis.computation_token),
            "catalog_total": len(analysis.items)}


def _status_url(token):
    return url_for("predictions.computation_status", token=token) if token else None


COMPUTATION_MESSAGES = {
    "pending": "Pronóstico en cola. El resultado aparecerá automáticamente cuando termine el cálculo.",
    "running": "Calculando pronóstico… Los datos observados ya están disponibles. El resultado aparecerá automáticamente.",
    "busy": "El cálculo predictivo está ocupado. Actualice en unos momentos para solicitar el pronóstico.",
    "error": "No se pudo completar el pronóstico. Actualice en unos momentos para reintentar; no se muestran resultados incompletos.",
    "unavailable": "El método seleccionado no produjo un pronóstico válido con los datos disponibles.",
    "expired": "El seguimiento ya no está disponible. Actualice para consultar el estado actual del pronóstico.",
}


def _deferred_view(builder):
    """Never describe incomplete predictive rankings/aggregates as final or as zero."""
    @wraps(builder)
    def build(*args, **kwargs):
        view = builder(*args, **kwargs)
        status = view["computation_status"]
        if status == "ready":
            return view
        message = COMPUTATION_MESSAGES[status]
        unavailable = status in ("error", "unavailable", "expired")
        label = "Pronóstico no disponible" if unavailable else "Pronóstico pendiente"
        operational = builder.__name__ == "critical_view"
        view["computation_message"] = message
        view["state"] = _state(False, "Calculando pronóstico" if status == "running" else label, message)
        for index, kpi in enumerate(view["kpis"]):
            if operational and index < 3:
                kpi["lines"] = [{"left": [fmt.seg("Solo riesgo operativo; cobertura predictiva pendiente", "muted")]}]
            else:
                kpi["value"] = fmt.ND
                kpi["lines"] = [{"left": [fmt.seg(label, "muted")]}]
        if builder.__name__ == "demand_view":
            view["kpis"][0].update(label="Productos disponibles", value=fmt.number(view["catalog_total"]))
            view["kpis"][0]["lines"] = [{"left": [fmt.seg(f"Catálogo completo; {label.lower()}", "muted")]}]
        if "donut" in view and not operational:
            view["donut"].update(center_value=fmt.ND, center_label="No disponible" if unavailable else "Pendiente", items=[], empty=True)
        if "top" in view and not operational:
            view["top"]["rows"] = []
        if "model_info" in view:
            view["model_info"]["rows"] = [("Estado", message)]
        if "recommendation" in view:
            view["recommendation"]["text"] = "Las recomendaciones requieren un pronóstico completo; no se generan órdenes automáticamente."
        view["excluded"] = []
        return view
    return build


# ---------------------------------------------------------------------------
# Pronóstico de demanda
# ---------------------------------------------------------------------------
@_deferred_view
def demand_view(args, export: bool = False) -> dict:
    p = parse_params(args, export)
    analysis = predictive_analysis_service.analyze(p.horizon, p.method, release_session=True, cache_predictions=True,
                                                  defer_predictions=True)
    items = [i for i in analysis.with_forecast if _matches(i, p)]
    items.sort(key=lambda i: (i.risk.rank, i.risk.days_of_cover if i.risk.days_of_cover is not None else 1e9))
    at_risk = [i for i in items if i.risk.level in (risk_service.RISK_CRITICAL, risk_service.RISK_HIGH)]
    needing = [i for i in items if i.recommendation and i.recommendation.needed]
    pooled = fmt.pooled_metrics(items)
    wape = pooled.wape if pooled else None
    total = len(analysis.items)
    kpis = [
        fmt.kpi("ti-trending-up", "blue", "Productos analizados", fmt.number(len(items)),
                [{"left": [fmt.seg(fmt.share(len(items), total), "blue")]}]),
        fmt.kpi("svg:shield-alert", "red", "En riesgo de quiebre", fmt.number(len(at_risk)),
                [{"left": [fmt.seg(f"de {len(items)} pronosticados", "red")]}]),
        fmt.kpi("svg:cart", "orange", "Requieren reposición", fmt.number(len(needing)),
                [{"left": [fmt.seg("Ver recomendaciones", "orange")]}], ),
        fmt.kpi("svg:target", "purple", "Error del modelo (WAPE)", fmt.pct(wape),
                [] if wape is not None else [{"left": [fmt.seg("Sin backtest evaluable", "muted")]}]),
    ]
    kpis[2]["href"] = url_for("pages.replenishment", **p.query(page=None))
    page_items, pagination = _paginate(items, p, "pages.demand_forecast", "productos")
    rows = [fmt.forecast_row(i) for i in page_items]
    filters = {"search": "Buscar por producto, categoría o código...", "search_value": p.search, "clear": True,
               "selects": [_select("Categoría", "category", _categories(analysis), p.category_id),
                           _select("Proveedor", "supplier", _suppliers(analysis), p.supplier_id),
                           _select("Horizonte", "horizon", _horizons(), p.horizon),
                           _select("Método", "method", METHOD_OPTIONS, p.method)]}
    other = 90 if p.horizon != 90 else 30
    view = {**_common(analysis, p, "pages.demand_forecast"), "kpis": kpis, "filters": filters, "rows": rows,
            "pagination": pagination, "table_title": f"Pronóstico de demanda (próximos {p.horizon} días)",
            "horizon": p.horizon, "donut": fmt.risk_donut(items, "Distribución de riesgo"),
            "top": {"title": "Top 5 con mayor demanda estimada", "rows": fmt.top_demand(items),
                    "toggle": [(f"{p.horizon} días", None), (f"{other} días", url_for("pages.demand_forecast", **p.query(horizon=other, page=None)))],
                    "link": "Ver todos los productos", "link_href": url_for("pages.demand_forecast", **p.query(per_page=50, page=None))},
            "model_info": {**fmt.model_info(analysis), "link": "Ver detalles del modelo",
                           "link_href": url_for("pages.model_accuracy", **p.query(page=None, per_page=None))},
            "state": _state(bool(items), "Pronóstico no disponible", FORECAST_MESSAGE, _hints(analysis)),
            "excluded": fmt.eligibility_breakdown(analysis)}
    return view


# ---------------------------------------------------------------------------
# Reabastecimiento
# ---------------------------------------------------------------------------
@_deferred_view
def replenishment_view(args, export: bool = False) -> dict:
    p = parse_params(args, export)
    analysis = predictive_analysis_service.analyze(p.horizon, p.method, release_session=True, cache_predictions=True,
                                                  defer_predictions=True)
    items = [i for i in analysis.with_forecast if i.recommendation and i.recommendation.needed and _matches(i, p)]
    if p.risk:  # el filtro "Prioridad" reutiliza el parámetro risk: high/medium/low
        items = [i for i in items if i.recommendation.priority == p.risk]
    items.sort(key=lambda i: (replenishment_service.PRIORITY_RANK[i.recommendation.priority],
                              i.recommendation.cover_days if i.recommendation.cover_days is not None else 1e9))
    from app.utils.permissions import response_includes_cost
    high = [i for i in items if i.recommendation.priority == replenishment_service.PRIORITY_HIGH]
    covers = [i.recommendation.cover_days for i in items if i.recommendation.cover_days is not None]
    avg_cover = sum(covers) / len(covers) if covers else None
    can_see_cost = response_includes_cost()
    kpis = [
        fmt.kpi("svg:cart", "orange", "Productos por reabastecer", fmt.number(len(items)),
                [{"left": [fmt.seg("Con pronóstico y necesidad neta > 0", "orange", True)]}]),
        fmt.kpi("ti-alert-triangle", "red", "Prioridad alta", fmt.number(len(high)),
                [{"left": [fmt.seg("Se agotarían antes de la reposición", "muted")]}]),
        fmt.kpi("ti-calendar", "blue", "Cobertura promedio", f"{avg_cover:.0f} días" if avg_cover is not None else fmt.ND,
                [{"left": [fmt.seg("Stock + supply en camino / demanda diaria", "muted")]}]),
    ]
    if can_see_cost:
        amount, explanation = fmt.replenishment_estimates(items)
        kpis.insert(2, fmt.kpi("ti-currency-dollar", "green", "Monto estimado de compra", amount,
                [{"left": [fmt.seg(explanation, "muted")]}]))
    page_items, pagination = _paginate(items, p, "pages.replenishment", "productos")
    rows = [fmt.replenishment_row(i) for i in page_items]
    priority_options = [("", "Todas")] + [(k, v) for k, v in replenishment_service.PRIORITY_LABELS.items()]
    filters = {"search": "Buscar producto por nombre o código...", "search_value": p.search, "clear": True,
               "selects": [_select("Categoría", "category", _categories(analysis), p.category_id),
                           _select("Proveedor", "supplier", _suppliers(analysis), p.supplier_id),
                           _select("Prioridad", "risk", priority_options, p.risk),
                           _select("Horizonte", "horizon", _horizons(), p.horizon)]}
    if can_see_cost:
        top = fmt.replenishment_cost_ranking(items)
        top_rows = [(fmt.product_cell(i.product), fmt.qty(i.recommendation.suggested_quantity, i.product),
                     fmt.money(i.recommendation.estimated_cost, getattr(i.product, "purchase_price_currency", None))) for i in top]
    else:
        top = sorted(items, key=lambda i: (-i.recommendation.suggested_quantity,
                                           replenishment_service.PRIORITY_RANK[i.recommendation.priority]))[:5]
        top_rows = [(fmt.product_cell(i.product), fmt.qty(i.recommendation.suggested_quantity, i.product),
                     None) for i in top]
    return {**_common(analysis, p, "pages.replenishment"), "kpis": kpis, "filters": filters, "rows": rows,
            "pagination": pagination, "table_title": "Recomendaciones de reabastecimiento", "horizon": p.horizon,
            "donut": fmt.priority_donut(items),
            "top": {"title": "Top 5 por moneda" if can_see_cost else "Top 5 compras sugeridas",
                    "rows": top_rows},
            "model_info": fmt.model_info(analysis, colon=True),
            "state": _state(bool(items), "Reabastecimiento predictivo no disponible", REPLENISHMENT_MESSAGE,
                            _hints(analysis) if not analysis.with_forecast else
                            ["Ningún producto con pronóstico tiene necesidad neta de compra con los filtros actuales."]),
            "suppliers": [],
            "can_order": role_has_permission(getattr(current_user, "role", None), REPLENISHMENT_CREATE),
            "can_see_cost": can_see_cost,
            "order_url": url_for("predictions.replenishment_order"),
            "today": analysis.today.isoformat()}


# ---------------------------------------------------------------------------
# Productos críticos
# ---------------------------------------------------------------------------
@_deferred_view
def critical_view(args, export: bool = False) -> dict:
    p = parse_params(args, export)
    analysis = predictive_analysis_service.analyze(p.horizon, p.method, release_session=True, cache_predictions=True,
                                                  defer_predictions=True)
    active = [i for i in analysis.items if i.product.is_active
              and getattr(i.product, "approval_status", "approved") == "approved"]
    flagged = [i for i in active if i.risk.level != risk_service.RISK_LOW or i.risk.high_rotation]
    items = [i for i in flagged if _matches(i, p)]
    if p.risk:
        items = [i for i in items if i.risk.level == p.risk]
    if p.status == "out_of_stock":
        items = [i for i in items if i.risk.out_of_stock]
    elif p.status == "low_stock":
        items = [i for i in items if i.risk.low_stock]
    elif p.status == "high_rotation":
        items = [i for i in items if i.risk.high_rotation]
    items.sort(key=lambda i: (i.risk.rank, i.risk.days_of_cover if i.risk.days_of_cover is not None else 1e9,
                              i.product.current_stock))
    total_active = len(active)
    has_forecast = any(i.forecast for i in active)
    kpis = [
        fmt.kpi("ti-alert-triangle", "red", "Productos en riesgo", fmt.number(len(items)),
                [{"left": [fmt.seg(f"de {total_active} productos activos", "muted")]}]),
        fmt.kpi("ti-cube", "red", "Críticos", fmt.number(sum(1 for i in items if i.risk.level == risk_service.RISK_CRITICAL)),
                [{"left": [fmt.seg("Nivel de riesgo crítico", "muted")]}]),
        fmt.kpi("ti-alert-triangle", "orange", "Bajo stock", fmt.number(sum(1 for i in items if i.risk.low_stock)),
                [{"left": [fmt.seg("Por debajo del stock mínimo", "muted")]}]),
        fmt.kpi("svg:chart-bar", "purple", "Rotación alta",
                fmt.number(sum(1 for i in items if i.risk.high_rotation)) if has_forecast else fmt.ND,
                [{"left": [fmt.seg(f"Demanda prevista ({p.horizon} d) > stock" if has_forecast else "Requiere pronóstico", "muted")]}]),
    ]
    page_items, pagination = _paginate(items, p, "pages.critical_products", "productos")
    rows = [fmt.critical_row(i) for i in page_items]
    filters = {"search": "Buscar producto por nombre o código...", "search_value": p.search, "clear": True,
               "selects": [_select("Categoría", "category", _categories(analysis), p.category_id),
                           _select("Estado", "status", STATUS_OPTIONS, p.status),
                           _select("Nivel de riesgo", "risk", RISK_OPTIONS, p.risk),
                           _select("Horizonte", "horizon", _horizons(), p.horizon)]}
    hints = ["El riesgo operativo (stock vs. stock mínimo) siempre se evalúa."]
    if not has_forecast:
        hints.append("El riesgo predictivo (días de cobertura) requiere historial suficiente.")
    return {**_common(analysis, p, "pages.critical_products"), "kpis": kpis, "filters": filters, "rows": rows,
            "pagination": pagination, "table_title": "Productos críticos", "horizon": p.horizon,
            "donut": fmt.level_donut(items, "Distribución por nivel de riesgo"),
            "top": {"title": "Top 5 productos por riesgo", "rows": fmt.critical_top(items)},
            "predictive_available": has_forecast,
            "recommendation": {"title": "Recomendación",
                               "text": ("Se recomienda generar una orden de compra para los productos en nivel crítico y alto "
                                        "a la brevedad posible." if items else
                                        "No hay productos activos en riesgo con los filtros actuales."),
                               "link": url_for("pages.replenishment", **p.query(page=None, risk=None, status=None)),
                               "link_text": "Ir a Reabastecimiento"},
            "state": _state(bool(items), "Sin productos en riesgo", "Ningún producto activo requiere atención por "
                            "stock mínimo, demanda prevista, punto de reorden o cobertura con estos filtros.", hints)}


# ---------------------------------------------------------------------------
# Tendencias
# ---------------------------------------------------------------------------
def trends_view(args, export: bool = False) -> dict:
    p = parse_params(args, export)
    config = trend_service.TrendConfig(period_days=p.period)
    analysis = predictive_analysis_service.analyze(p.horizon, p.method, release_session=True,
                                                   observed_only=True, trend_config=config)
    classified = []
    for item in analysis.items:
        if item.series.positive_periods and item.product.is_active:
            if item.trend.status != trend_service.TREND_INSUFFICIENT:
                classified.append(item)
    total_active = sum(1 for i in analysis.items if i.product.is_active)
    counts = {s: sum(1 for i in classified if i.trend.status == s) for s in (trend_service.TREND_UP, trend_service.TREND_DOWN,
                                                                             trend_service.TREND_STABLE)}
    avg = fmt.average_variation(classified)
    items = [i for i in classified if _matches(i, p)]
    if p.trend != "all":
        items = [i for i in items if i.trend.status == p.trend]
    items.sort(key=fmt.trend_sort_key(p.trend))
    stable_kpi = fmt.kpi("ti-arrow-right", "gray", "Demanda estable", fmt.number(counts["stable"]),
                         [{"left": [fmt.seg(fmt.share(counts["stable"], total_active), "muted")]}])
    stable_kpi["icon_tone"] = "dark"
    kpis = [
        fmt.kpi("ti-trending-up", "green", "Productos en tendencia al alza", fmt.number(counts["up"]),
                [{"left": [fmt.seg(fmt.share(counts["up"], total_active), "muted")]}]),
        fmt.kpi("ti-trending-down", "red", "Productos en tendencia a la baja", fmt.number(counts["down"]),
                [{"left": [fmt.seg(fmt.share(counts["down"], total_active), "muted")]}]),
        stable_kpi,
        fmt.kpi("svg:chart-bar", "purple", "Variación promedio de ventas", fmt.pct(avg, signed=True),
                [{"left": [fmt.seg(f"Últimos {p.period} días vs. {p.period} anteriores", "blue")]}],
                value_tone=("green" if avg is not None and avg > 0 else "red" if avg is not None and avg < 0 else None)),
    ]
    page_items, pagination = _paginate(items, p, "pages.trends", "productos")
    rows = [fmt.trend_row(i) for i in page_items]
    titles = {"up": "Productos en tendencia al alza", "down": "Productos en tendencia a la baja",
              "stable": "Productos con tendencia estable", "all": "Resumen general de tendencias"}
    top_specs = {"up": ("Top 5 productos con mayor crecimiento", "green", "up", trend_service.TREND_UP),
                 "down": ("Top 5 productos con mayor caída", "red", "down", trend_service.TREND_DOWN),
                 "stable": ("Top 5 productos con mayor estabilidad", "blue", None, trend_service.TREND_STABLE),
                 "all": ("Top 5 productos con mayor caída", "red", "down", trend_service.TREND_DOWN)}
    title, tone, arrow, status = top_specs[p.trend]
    ranked = sorted((i for i in classified if i.trend.status == status), key=fmt.trend_sort_key(status))[:5]
    top_rows = [(fmt.product_cell(i.product), fmt.pct(i.trend.variation_pct, signed=status != trend_service.TREND_STABLE)
                 if i.trend.variation_pct is not None else "Nuevo") for i in ranked]
    chart = trend_service.category_evolution({i.product.id: i.series for i in classified},
                                             {i.product.id: i.category_name for i in classified}, analysis.as_of, config)
    for series, tone_name in zip(chart["series"], fmt.tones_for_series(len(chart["series"]))):
        series["tone"] = tone_name
    peak = max((v for s in chart["series"] for v in s["values"]), default=0.0)
    chart["y_step"] = max(1.0, round(peak / 4)) if peak else 1.0
    chart["y_max"] = chart["y_step"] * 4 if peak else 4.0
    trend_options = [("all", "Todas")] + [(k, v) for k, v in TREND_TABS if k != "all"]
    filters = {"search": "Buscar producto por nombre o código...", "search_value": p.search, "clear": False,
               "selects": [_select("Categoría", "category", _categories(analysis), p.category_id),
                           _select("Tendencia", "trend", trend_options, p.trend),
                           _select("Período de análisis", "period", [(str(d), f"Últimos {d} días") for d in TREND_PERIODS], p.period)]}
    info = (f"Las tendencias comparan la demanda observada de los últimos {p.period} días con los {p.period} días "
            f"anteriores; |variación| ≤ {trend_service.DEFAULT_CONFIG.stable_tolerance_pct:.0f}% se considera estable. "
            "El patrón detectado describe la serie, no causas externas.")
    return {**_common(analysis, p, "pages.trends"), "kpis": kpis, "filters": filters, "rows": rows,
            "pagination": pagination, "table_title": titles[p.trend], "trend_state": p.trend,
            "tabs": [(k, v, url_for("pages.trends", **p.query(trend=k if k != "up" else None, page=None))) for k, v in TREND_TABS],
            "chart": chart, "top": {"title": title, "tone": tone, "arrow": arrow, "rows": top_rows}, "info": info,
            "period": p.period,
            "state": _state(bool(items), "Tendencias no disponibles", TRENDS_MESSAGE,
                            ["Se requieren al menos dos períodos con demanda entre la ventana actual y la anterior."]
                            if not classified else ["Ningún producto coincide con los filtros aplicados."])}


# ---------------------------------------------------------------------------
# Precisión del modelo
# ---------------------------------------------------------------------------
def _pairs(items):
    actual, predicted = [], []
    for item in items:
        actual.extend(item.backtest.actual)
        predicted.extend(item.backtest.predicted)
    return actual, predicted


def _metric_rows(items) -> list[tuple]:
    pooled = fmt.pooled_metrics(items)
    if pooled is None:
        return []
    mape_text = f"{fmt.pct(pooled.mape)} ({pooled.mape_n} pares; {pooled.mape_excluded} con real = 0 excluidos)" \
        if pooled.mape is not None else "N/D (todos los reales en 0)"
    return [("WAPE", "Error porcentual absoluto ponderado", fmt.pct(pooled.wape), fmt.error_verdict(pooled.wape, 20, 35)),
            ("MAE", "Error absoluto medio por período", fmt.number(pooled.mae, 2), ("Unidades", "gray")),
            ("RMSE", "Raíz del error cuadrático medio", fmt.number(pooled.rmse, 2), ("Unidades", "gray")),
            ("ME (Sesgo)", "Error medio (predicho − real)", fmt.number(pooled.bias, 2), fmt.bias_verdict(pooled.bias, pooled.mae)),
            ("MAPE", "Error porcentual absoluto medio (solo reales ≠ 0)", mape_text, fmt.error_verdict(pooled.mape, 20, 35))]


def _group_rows(items, key, label) -> list[dict]:
    groups: dict[str, list] = {}
    for item in items:
        groups.setdefault(key(item), []).append(item)
    rows = []
    for name, members in sorted(groups.items()):
        pooled = fmt.pooled_metrics(members)
        rows.append({"name": name, "count": len(members), "n": pooled.n, "wape": fmt.pct(pooled.wape),
                     "mae": fmt.number(pooled.mae, 2), "rmse": fmt.number(pooled.rmse, 2), "bias": fmt.number(pooled.bias, 2),
                     "verdict": fmt.error_verdict(pooled.wape, 20, 35), "label": label,
                     "model": ", ".join(sorted({m.forecast.model_label for m in members}))})
    return sorted(rows, key=lambda r: float(r["wape"].rstrip("%")) if r["wape"] != fmt.ND else 1e9, reverse=True)


def _error_patterns(items) -> list[tuple[str, str, str]]:
    total = len(items)
    if not total:
        return []
    intermittent = sum(1 for i in items if (i.series.zero_ratio or 0) >= 0.5)
    short = sum(1 for i in items if i.series.periods < 2 * data.ELIGIBILITY_CONFIG[i.series.granularity].min_periods)
    volatile = 0
    for i in items:
        values = i.series.values
        mean = sum(values) / len(values) if values else 0
        if mean > 0 and (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5 / mean >= 1.0:
            volatile += 1
    errors = sorted((sum(abs(p - a) for a, p in zip(i.backtest.actual, i.backtest.predicted)) for i in items), reverse=True)
    top_share = sum(errors[:max(1, total // 5)]) / sum(errors) * 100 if sum(errors) else 0.0

    def row(label, n, tone):
        return (label, f"{n / total * 100:.1f}%", tone)

    return [row("Demanda intermitente (≥ 50% de períodos en 0)", intermittent, "red"),
            row("Alta volatilidad (CV ≥ 1)", volatile, "orange"),
            row("Historial corto", short, "orange"),
            ("Error concentrado en el 20% de productos", f"{top_share:.1f}%", "red" if top_share > 60 else "green")]


@_deferred_view
def accuracy_view(args, export: bool = False) -> dict:
    p = parse_params(args, export)
    analysis = predictive_analysis_service.analyze(p.horizon, p.method, release_session=True, cache_predictions=True,
                                                  defer_predictions=True)
    items = [i for i in analysis.evaluated if _matches(i, p)]
    pooled = fmt.pooled_metrics(items)
    wape = pooled.wape if pooled else None
    folds = sum(len(i.backtest.folds) for i in items)
    bias_label, bias_tone = fmt.bias_verdict(pooled.bias if pooled else None, pooled.mae if pooled else None)
    wape_label, wape_tone = fmt.error_verdict(wape, 20, 35)
    kpis = [
        fmt.kpi("ti-target", "blue", "Error global (WAPE)", fmt.pct(wape),
                [{"left": [fmt.seg(wape_label, wape_tone if wape is not None else "muted", True)]}],
                value_tone="green" if wape_tone == "green" else None),
        fmt.kpi("ti-wave-sine", "purple", "Error absoluto medio (MAE)", fmt.number(pooled.mae, 1) if pooled else fmt.ND,
                [{"left": [fmt.seg("Unidades por período", "muted", True)]}, {"left": [fmt.seg("Fuera de muestra", "muted")]}],
                value_tone="purple"),
        fmt.kpi("ti-circle-check", "green", "Sesgo promedio (ME)", fmt.number(pooled.bias, 2) if pooled else fmt.ND,
                [{"left": [fmt.seg(bias_label, bias_tone, True)]}, {"left": [fmt.seg("Predicho − real", "muted")]}],
                value_tone="green" if bias_tone == "green" else None),
        fmt.kpi("ti-activity", "orange", "Predicciones evaluadas", fmt.number(pooled.n) if pooled else "0",
                [{"left": [fmt.seg(f"{len(items)} productos · {folds} pliegues", "blue", True)]},
                 {"left": [fmt.seg(f"Horizonte {p.horizon} días", "muted")]}], value_tone="orange"),
    ]
    actual, predicted = _pairs(items)
    # Gráfico: último pliegue fuera de muestra de cada producto, sumado por período relativo.
    horizon_periods = max((len(i.backtest.last_fold.actual) for i in items), default=0)
    real_line, pred_line = [0.0] * horizon_periods, [0.0] * horizon_periods
    for item in items:
        fold = item.backtest.last_fold
        for k, (a, pr) in enumerate(zip(fold.actual, fold.predicted)):
            real_line[k] += a
            pred_line[k] += pr
    starts = next((i.series.starts[i.backtest.last_fold.origin:] for i in items), [])
    labels = [d.strftime("%d/%m") if (k % max(1, horizon_periods // 6) == 0 or k == horizon_periods - 1) else ""
              for k, d in enumerate(starts[:horizon_periods])]
    y_max = max([*real_line, *pred_line, 1.0])
    step = max(1.0, round(y_max / 5))
    chart = {"title": f"Predicción vs. Real (último pliegue, {p.horizon} días)", "labels": labels,
             "real": [round(v, 2) for v in real_line], "pred": [round(v, 2) for v in pred_line],
             "y_max": step * 6, "y_step": step, "y_label": "Unidades",
             "legend": [("Valores reales", "solid"), ("Valores predichos", "dashed")]}
    distribution = metrics.ape_distribution(actual, predicted) if actual else []
    within_20 = sum(d["share"] or 0 for d in distribution[:2]) if distribution else None
    ape = {"title": "Distribución del error porcentual (APE)",
           "segments": [(d["label"], fmt.pct(d["share"]), d["tone"], d["share"] or 0) for d in distribution],
           "note": (f"El {within_20:.1f}% de las predicciones (reales ≠ 0) tienen un error menor o igual al 20%."
                    if within_20 is not None else "Sin pares evaluables.")}
    tab_rows = []
    if p.tab == "category":
        tab_rows = _group_rows(items, lambda i: i.category_name, "categoría")
    elif p.tab == "product":
        tab_rows = _group_rows(items, lambda i: f"{i.product.name} ({i.product.code})", "producto")
    page_rows, pagination = _paginate(tab_rows if p.tab != "summary" else _metric_rows(items), p, "pages.model_accuracy",
                                      "métricas" if p.tab == "summary" else ("categorías" if p.tab == "category" else "productos"))
    method_options = [(forecast_service.AUTO_MODEL, "Mejor por backtest")] + list(forecast_service.MODEL_LABELS.items())
    filters = {"search": "Buscar producto por nombre o código...", "search_value": p.search, "clear": False,
               "selects": [_select("Categoría", "category", _categories(analysis), p.category_id),
                           _select("Modelo", "method", method_options, p.method),
                           _select("Período de evaluación", "horizon", [(str(h), f"Horizonte {h} días") for h in forecast_service.HORIZONS_DAYS], p.horizon)]}
    return {**_common(analysis, p, "pages.model_accuracy"), "kpis": kpis, "filters": filters, "rows": page_rows,
            "pagination": pagination, "tab": p.tab,
            "tabs": [(k, v, url_for("pages.model_accuracy", **p.query(tab=k if k != "summary" else None, page=None))) for k, v in ACCURACY_TABS],
            "metrics_title": "Métricas de error (backtest fuera de muestra)", "chart": chart, "ape": ape,
            "errors": {"title": "Patrones asociados al error", "rows": _error_patterns(items)},
            "info": {"title": "Información", "text": "Las métricas provienen exclusivamente de predicciones fuera de muestra "
                     "(rolling-origin): el modelo se ajusta con el pasado y se evalúa contra períodos posteriores. "
                     "MAPE y WAPE son métricas de error."},
            "state": _state(bool(items), "Precisión no disponible", ACCURACY_MESSAGE,
                            ["Se requieren productos elegibles (≥ 30 períodos, ≥ 8 con demanda) y al menos un pliegue temporal."])}


# ---------------------------------------------------------------------------
# Exportación CSV (resultados LIVE filtrados)
# ---------------------------------------------------------------------------
EXPORTS = {
    "demand_forecast": (demand_view, "pronostico-demanda", [("p_name", "Producto"), ("p_sku", "Código"), ("cat", "Categoría"),
                        ("stock", "Stock actual"), ("demand", "Demanda estimada"), ("delta", "Intervalo"), ("risk_label", "Riesgo"),
                        ("risk_note", "Nota de riesgo"), ("buy", "Sugerencia"), ("when", "Fecha"), ("model", "Método")]),
    "replenishment": (replenishment_view, "reabastecimiento", [("p_name", "Producto"), ("p_sku", "Código"), ("cat", "Categoría"),
                      ("stock", "Stock actual"), ("demand", "Demanda estimada"), ("reorder", "Punto de reorden"), ("qty", "Cantidad sugerida"),
                      ("prio_label", "Prioridad"), ("date", "Fecha recomendada"), ("cost", "Costo estimado"), ("pending", "Supply pendiente")]),
    "critical_products": (critical_view, "productos-criticos", [("p_name", "Producto"), ("p_sku", "Código"), ("cat", "Categoría"),
                          ("stock", "Stock actual"), ("minimum", "Stock mínimo"), ("demand", "Demanda estimada"), ("cover", "Días de cobertura"),
                          ("risk_label", "Nivel de riesgo"), ("motive", "Motivo"), ("kind", "Tipo de riesgo")]),
    "trends": (trends_view, "tendencias", [("p_name", "Producto"), ("p_sku", "Código"), ("cat", "Categoría"), ("sales", "Ventas período actual"),
               ("prev", "Ventas período anterior"), ("variation", "Variación"), ("badge_label", "Tendencia"), ("cause", "Patrón detectado")]),
    "model_accuracy": (accuracy_view, "precision-modelo", [("name", "Grupo"), ("count", "Productos"), ("n", "Pares evaluados"),
                       ("wape", "WAPE"), ("mae", "MAE"), ("rmse", "RMSE"), ("bias", "Sesgo"), ("model", "Modelo")]),
}


def _flatten(row: dict) -> dict:
    flat = dict(row)
    if "p" in row:
        flat["p_name"], flat["p_sku"] = row["p"]["name"], row["p"]["sku"]
    for key in ("risk", "prio", "badge"):
        if key in row and isinstance(row[key], tuple):
            flat[f"{key}_label"] = row[key][0]
    if flat.get("buy") is None and "buy" in flat:
        flat["buy"] = "No requiere compra"
    return flat


def export(page: str):
    if request.args.get("ref") == "1":
        raise ValidationError("La referencia no exporta resultados LIVE.", status_code=404)
    if page not in EXPORTS:
        raise ValidationError("Vista de exportación desconocida.", status_code=404)
    builder, stem, columns = EXPORTS[page]
    args = request.args.to_dict()
    args["page"] = "1"
    if page == "model_accuracy" and args.get("tab", "summary") == "summary":
        args["tab"] = "product"
    # export=True desactiva la paginación: se exportan todos los resultados filtrados, nunca fixtures.
    view = builder(args, export=True)
    if view["computation_status"] != "ready":
        from flask import jsonify

        response = jsonify(status=view["computation_status"], message=view["computation_message"])
        response.status_code = 202 if view["computation_status"] in ("pending", "running", "busy") else 503
        response.headers["Retry-After"] = "30"
        response.headers["Cache-Control"] = "no-store"
        return response
    from app.utils.permissions import response_includes_cost
    if page == "replenishment" and not response_includes_cost():
        columns = [col for col in columns if col[0] != "cost"]
    records = [_flatten(r) for r in view["rows"]]
    spec = [(key, label, 20) for key, label in columns]
    for record in records:
        for key, _, _ in spec:
            record.setdefault(key, "")
    now = datetime.now(COMMERCIAL_ZONE)
    response = send_file(csv_file(records, spec, now), mimetype="text/csv; charset=utf-8", as_attachment=True,
                         download_name=f"{stem}_{now:%Y-%m-%d}.csv")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# ---------------------------------------------------------------------------
# Solicitud de reposición desde recomendaciones (confirmación humana; permiso de reposición)
# ---------------------------------------------------------------------------
def replenishment_order():
    """Crea UNA solicitud de reposición sin precios. No crea la orden de compra."""
    from app.controllers.purchase_controller import _mutation_guard
    from app.controllers.sale_controller import _json_body
    from app.services import replenishment_request_service

    _mutation_guard()
    try:
        validate_csrf(request.headers.get("X-CSRFToken"))
    except CsrfValidationError:
        raise ValidationError("Token CSRF inválido o ausente.", status_code=400)
    body = _json_body()
    if not isinstance(body, dict) or body.get("confirm") is not True:
        raise ValidationError("La solicitud requiere confirmación explícita del usuario.")
    notes = str(body.get("notes") or "")[:900]
    from app.controllers.purchase_controller import _mutate
    return _mutate(replenishment_request_service.create_from_selection, {**body, "notes": notes}, status=201)


def product_detail(product_id):
    """One read-only product DTO, independent of table filters and pagination."""
    from flask import current_app, jsonify
    from app.services import predictive_product_detail_service
    from app.utils.permissions import SALES_READ, INVENTORY_READ, PURCHASES_READ, PRODUCTS_READ, response_includes_cost
    from app.utils.predictive_detail_reference import detail

    role = getattr(current_user, "role", None)
    allowed = lambda permission: role_has_permission(role, permission)
    if request.args.get("ref") == "1":
        if not current_app.debug:
            return jsonify(error="Referencia no disponible."), 404
        view = detail(request.args.get("sku", ""))
    else:
        view = predictive_product_detail_service.build(product_id, can_sales=allowed(SALES_READ),
                   can_inventory=allowed(INVENTORY_READ), can_buy=allowed(REPLENISHMENT_CREATE),
                   can_purchases=allowed(PURCHASES_READ), release_session=True, cache_predictions=True,
                   defer_predictions=True)
        if view is not None and not response_includes_cost():
            rec = view.get("recommendation") or {}
            rec.pop("unit_cost", None)
            rec.pop("unit_cost_currency", None)
            rec.pop("cost_source", None)
            preferred = view.get("preferred_supplier") or {}
            preferred.pop("purchase_price", None)
            preferred.pop("purchase_price_currency", None)
    if view is None:
        return jsonify(error="Producto no encontrado."), 404
    view["computation_status_url"] = _status_url(view.pop("computation_token", None))
    qs = {"ref": 1} if view["reference"] else {}
    view["links"] = {
        "inventory": url_for("pages.products", search=view["product"]["sku"], **qs) if allowed(PRODUCTS_READ) else None,
        "sales": url_for("pages.sales_list", search=view["product"]["sku"], **qs) if allowed(SALES_READ) else None,
        "movements": url_for("pages.inventory", product_id=product_id, **qs) if allowed(INVENTORY_READ) else None,
        "model": url_for("pages.model_accuracy", **qs),
    }
    return jsonify(view)


def computation_status(token):
    """Observe an existing job only; polling does not load series or schedule fits."""
    from flask import jsonify
    from app.services import predictive_job_service

    if request.args.get("ref") == "1" or len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
        return jsonify(error="Seguimiento no disponible."), 404
    state = predictive_job_service.prediction_status(token)
    state["message"] = COMPUTATION_MESSAGES.get(state["status"], "Pronóstico listo.")
    if state.get("timed_out"):
        state["message"] = ("El seguimiento superó el tiempo máximo. El cálculo puede continuar en segundo plano; "
                            "actualice más tarde para consultar el resultado.")
    response = jsonify(state)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Retry-After"] = str(state["retry_after"])
    return response
