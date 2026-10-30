"""Formato de presentación del módulo predictivo (LIVE).

Convierte los resultados de ``predictive_analysis_service`` en las mismas estructuras que
consumen las macros de ``templates/predictive`` (kpis, rows, donut, top, model_info). Solo
formatea: nada de reglas de negocio ni de fixtures. Todo valor no calculable se muestra
como "N/D" o con un estado explícito.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services import critical_product_service as risk_service, forecast_service
from app.services import predictive_data_service as data, predictive_metrics_service as metrics
from app.services import replenishment_service, trend_service
from app.services.predictive_analysis_service import Analysis, ProductAnalysis

ND = "N/D"
UNIT_ABBR = {"unidad": "uds.", "unidades": "uds.", "und": "uds.", "pieza": "uds.", "metro": "m", "metros": "m",
             "m": "m", "kilogramo": "kg", "kilogramos": "kg", "kg": "kg", "saco": "sacos", "sacos": "sacos",
             "litro": "L", "litros": "L", "m2": "m²", "caja": "cajas", "cajas": "cajas", "rollo": "rollos"}
RISK_UI_LABELS = {risk_service.RISK_CRITICAL: "Crítico", risk_service.RISK_HIGH: "Alto",
                  risk_service.RISK_MEDIUM: "Medio", risk_service.RISK_LOW: "Bajo"}
# El pronóstico agrupa Crítico+Alto como "Alto riesgo" (tres tonos, como la referencia).
FORECAST_RISK = {risk_service.RISK_CRITICAL: ("Alto", "red"), risk_service.RISK_HIGH: ("Alto", "red"),
                 risk_service.RISK_MEDIUM: ("Medio", "yellow"), risk_service.RISK_LOW: ("Bajo", "green")}
DEMAND_PATTERN_LABELS = {"regular": "Relativamente regular", "intermittent": "Intermitente",
                        "extremely_intermittent": "Extremadamente intermitente", "unavailable": "No disponible"}
PREDICTION_STATUS_LABELS = {"available": "Disponible", "limited": "Limitado", "abstain": "Abstención"}


def seg(text, tone=None, bold=False, arrow=None, size=None):
    return {"text": text, "tone": tone, "bold": bold, "arrow": arrow, "size": size}


def kpi(icon, tone, label, value, lines, value_tone=None, value_size=None):
    return {"icon": icon, "tone": tone, "label": label, "value": value, "value_tone": value_tone,
            "value_size": value_size, "lines": lines}


def unit_abbr(product) -> str:
    unit = (product.unit or "").strip().lower()
    return UNIT_ABBR.get(unit, product.unit or "uds.")


def qty(value, product, decimals: int = 0) -> str:
    if value is None:
        return ND
    number = f"{float(value):,.{decimals}f}"
    return f"{number} {unit_abbr(product)}"


def number(value, decimals: int = 0) -> str:
    return ND if value is None else f"{float(value):,.{decimals}f}"


def money(value, currency=None) -> str:
    from app.utils.purchase_currency import format_amount
    return ND if value is None else format_amount(value, currency)


def replenishment_estimates(items):
    totals, missing = {}, 0
    for item in items:
        currency = getattr(item.product, "purchase_price_currency", None)
        if currency not in ("USD", "VES"):
            missing += 1
            continue
        totals[currency] = totals.get(currency, Decimal(0)) + item.recommendation.estimated_cost
    text = " · ".join(money(amount, currency) for currency, amount in sorted(totals.items())) or ND
    note = "Costo de referencia × cantidad sugerida; totales por moneda"
    if missing:
        note += f"; {missing} sin moneda, excluidos"
    return text, note


def replenishment_cost_ranking(items):
    result = []
    for currency in ("USD", "VES"):
        group = [item for item in items if getattr(item.product, "purchase_price_currency", None) == currency]
        result.extend(sorted(group, key=lambda item: -item.recommendation.estimated_cost)[:5])
    return result


def pct(value, decimals: int = 1, signed: bool = False) -> str:
    if value is None:
        return ND
    return f"{float(value):+.{decimals}f}%" if signed else f"{float(value):.{decimals}f}%"


def day(value: date | None) -> str:
    return value.strftime("%d/%m/%Y") if value else ND


def product_cell(product) -> dict:
    return {"name": product.name, "sku": product.code, "src": product.image_url, "thumb": None, "id": product.id}


def share(part: int, total: int) -> str:
    return f"{(part / total * 100.0) if total else 0.0:.1f}% del catálogo"


def stock_state(item: ProductAnalysis) -> tuple[str, str]:
    product = item.product
    if product.current_stock <= 0:
        return "Agotado", "red"
    if product.current_stock <= product.minimum_stock:
        return "Bajo", "red"
    if item.risk.days_of_cover is not None and item.risk.days_of_cover <= risk_service.DEFAULT_CONFIG.medium_cover_days:
        return "Moderado", "orange"
    return "Suficiente", "green"


def cover_text(item: ProductAnalysis) -> str:
    return risk_service.cover_label(item.risk.days_of_cover, item.forecast is not None,
                                   zero_demand=item.forecast is not None and item.forecast.total == 0)


def demand_text(forecast, product) -> str:
    if forecast is None:
        return risk_service.NO_FORECAST_LABEL
    value = forecast.total
    amount = "< 0,01" if 0 < value < 0.01 else f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{amount} {unit_abbr(product)}"


# ---------------------------------------------------------------------------
# Pronóstico de demanda
# ---------------------------------------------------------------------------
def forecast_row(item: ProductAnalysis) -> dict:
    fc, rec, risk = item.forecast, item.recommendation, item.risk
    product = item.product
    experimental = item.experimental or {}
    profile = experimental.get("demand_profile") or {}
    policy = experimental.get("prediction_policy") or {}
    if fc.half_width is not None:
        delta = f"± {qty(fc.half_width, product)}"
    elif fc.kind == "descriptive":
        delta = "Estimación descriptiva"
    else:
        delta = "Sin intervalo (backtest insuficiente)"
    if risk.days_of_cover is None:
        risk_note = risk_service.NO_DEMAND_LABEL if fc.daily_rate <= 0 else "Sin stock"
    elif risk.level == risk_service.RISK_LOW:
        risk_note = "Sin riesgo"
    else:
        risk_note = f"Quiebre en {int(risk.days_of_cover)} días" if risk.days_of_cover >= 1 else "Quiebre inminente"
    label, tone = FORECAST_RISK[risk.level]
    needed = rec is not None and rec.needed
    return {"id": product.id, "p": product_cell(product), "cat": item.category_name,
            "demand_pattern": DEMAND_PATTERN_LABELS.get(profile.get("zero_rate_segment"), ND),
            "prediction_status": PREDICTION_STATUS_LABELS.get(policy.get("status"), ND),
            "stock": qty(product.current_stock, product), "stock_state": stock_state(item),
            "demand": qty(fc.total, product), "delta": delta, "risk": (label, tone), "risk_note": risk_note,
            "buy": f"Comprar {qty(rec.suggested_quantity, product)}" if needed else None,
            "when": f"Para el {day(rec.recommended_date)}" if needed else "No requiere compra",
            "eligibility": item.eligibility.label, "model": fc.model_label if fc.kind == "model" else "Tasa media (descriptiva)"}


def risk_donut(items: list[ProductAnalysis], title: str) -> dict:
    total = len(items)
    high = sum(1 for i in items if i.risk.level in (risk_service.RISK_CRITICAL, risk_service.RISK_HIGH))
    medium = sum(1 for i in items if i.risk.level == risk_service.RISK_MEDIUM)
    low = total - high - medium

    def part(n):
        return f"{n} ({(n / total * 100.0) if total else 0.0:.1f}%)"

    return {"title": title, "center_value": number(total), "center_label": "Productos",
            "items": [("Alto riesgo", part(high), "red", high), ("Riesgo medio", part(medium), "orange", medium),
                      ("Bajo riesgo", part(low), "green", low)], "empty": total == 0}


def top_demand(items: list[ProductAnalysis], limit: int = 5) -> list[tuple[str, int, str]]:
    ranked = sorted(items, key=lambda i: -i.forecast.total)[:limit]
    top = ranked[0].forecast.total if ranked else 0
    return [(i.product.name, int(round(i.forecast.total / top * 100)) if top else 0, qty(i.forecast.total, i.product))
            for i in ranked]


def model_summary(analysis: Analysis) -> str:
    if analysis.model != forecast_service.AUTO_MODEL:
        return forecast_service.MODEL_LABELS[analysis.model]
    used = analysis.models_used()
    if not used:
        return "Selección por backtest (sin productos elegibles)"
    parts = ", ".join(f"{forecast_service.MODEL_LABELS[m]} ({n})" for m, n in sorted(used.items(), key=lambda x: -x[1]))
    return f"Selección por backtest: {parts}"


def data_summary(analysis: Analysis) -> str:
    start = analysis.history_start
    if start is None:
        return "Sin ventas registradas"
    days = (analysis.today - start).days + 1
    sources = {i.series.data_source for i in analysis.items if i.series.data_source}
    label = {"historical": "históricos confirmados", "operational": "ventas y notas del sistema",
             "combined": "históricos + ventas del sistema"}
    source = ", ".join(sorted(label.get(s, s) for s in sources)) or "ventas"
    return f"Demanda observada ({source}) desde {day(start)} ({days} días)"


def backtest_summary(analysis: Analysis) -> str:
    evaluated = analysis.evaluated
    if not evaluated:
        return "Sin pliegues evaluables todavía"
    folds = sum(len(i.backtest.folds) for i in evaluated)
    return f"Rolling-origin: {len(evaluated)} productos, {folds} pliegues, horizonte {analysis.horizon_days} días"


def model_info(analysis: Analysis, title: str = "Información del modelo", colon: bool = False) -> dict:
    sep = ":" if colon else ""
    rows = [(f"Última actualización{sep}", analysis.generated_at.strftime("%d/%m/%Y %I:%M %p").lower()),
            (f"Método{sep}", model_summary(analysis)),
            (f"Datos utilizados{sep}", data_summary(analysis)),
            (f"Validación{sep}", backtest_summary(analysis))]
    return {"title": title, "rows": rows}


# ---------------------------------------------------------------------------
# Métricas globales (solo out-of-sample)
# ---------------------------------------------------------------------------
def pooled_metrics(items: list[ProductAnalysis]) -> metrics.MetricSet | None:
    actual, predicted = [], []
    for item in items:
        if item.backtest is not None:
            actual.extend(item.backtest.actual)
            predicted.extend(item.backtest.predicted)
    return metrics.compute(actual, predicted) if actual else None


def error_verdict(value: float | None, good: float, fair: float) -> tuple[str, str]:
    if value is None:
        return ND, "gray"
    if value <= good:
        return "Bajo", "green"
    if value <= fair:
        return "Moderado", "orange"
    return "Alto", "red"


def bias_verdict(bias_value: float | None, mae_value: float | None) -> tuple[str, str]:
    if bias_value is None or mae_value is None:
        return ND, "gray"
    if mae_value == 0 or abs(bias_value) <= 0.1 * mae_value:
        return "Sin sesgo significativo", "green"
    return ("Sobreestima", "orange") if bias_value > 0 else ("Subestima", "orange")


# ---------------------------------------------------------------------------
# Productos críticos
# ---------------------------------------------------------------------------
def critical_row(item: ProductAnalysis) -> dict:
    product, risk = item.product, item.risk
    has_fc = item.forecast is not None
    return {"id": product.id, "p": product_cell(product), "cat": item.category_name,
            "stock": qty(product.current_stock, product), "stock_tone": "red" if risk.out_of_stock or risk.low_stock else None,
            "minimum": qty(product.minimum_stock, product),
            "demand": demand_text(item.forecast, product),
            "forecast_status": item.forecast_status, "forecast_reason": item.forecast_reason,
            "cover": cover_text(item), "cover_tone": "red" if has_fc and risk.level == risk_service.RISK_CRITICAL else
            ("orange" if risk.level == risk_service.RISK_HIGH else None),
            "risk": (risk.label, risk.tone), "motive": risk.motive, "kind": risk.kind,
            "reasons": list(risk.reasons), "reorder_point": risk.reorder_point}


def level_donut(items: list[ProductAnalysis], title: str, levels=risk_service.RISK_ORDER) -> dict:
    counts = {level: sum(1 for i in items if i.risk.level == level) for level in levels}
    total = sum(counts.values())
    return {"title": title, "center_value": number(total), "center_label": "Productos",
            "items": [(RISK_UI_LABELS[level], f"{n} ({(n / total * 100.0) if total else 0.0:.1f}%)",
                       risk_service.RISK_TONES[level], n) for level, n in counts.items()], "empty": total == 0}


def critical_top(items: list[ProductAnalysis], limit: int = 5) -> list[dict]:
    ranked = sorted(items, key=lambda i: (i.risk.rank, i.risk.days_of_cover if i.risk.days_of_cover is not None else 1e9,
                                          i.product.current_stock))[:limit]
    result = []
    for item in ranked:
        product = item.product
        sub = "Sin stock" if item.risk.out_of_stock else f"{qty(product.current_stock, product)} (mín. {product.minimum_stock})"
        result.append({"p": product_cell(product), "badge": (item.risk.label, item.risk.tone), "sub": sub})
    return result


# ---------------------------------------------------------------------------
# Reabastecimiento
# ---------------------------------------------------------------------------
def replenishment_row(item: ProductAnalysis) -> dict:
    from app.utils.permissions import response_includes_cost
    product, rec, fc = item.product, item.recommendation, item.forecast
    row = {"id": product.id, "p": product_cell(product), "cat": item.category_name,
           "stock": qty(product.current_stock, product), "demand": qty(fc.total, product),
           "reorder": qty(rec.reorder_point, product), "qty": qty(rec.suggested_quantity, product),
           "quantity": rec.suggested_quantity,
           "prio": (rec.priority_label, rec.priority_tone), "date": day(rec.recommended_date),
           "pending": rec.pending_supply, "reasons": list(rec.reasons)}
    if response_includes_cost():
        row["unit_cost"] = f"{rec.unit_cost:.2f}"
        row["unit_cost_currency"] = getattr(product, "purchase_price_currency", None)
        row["cost_source"] = "product_reference"
        row["cost"] = money(rec.estimated_cost, row["unit_cost_currency"])
    return row


def priority_donut(items: list[ProductAnalysis]) -> dict:
    counts = {p: sum(1 for i in items if i.recommendation.priority == p) for p in replenishment_service.PRIORITY_RANK}
    total = sum(counts.values())
    return {"title": "Distribución de prioridad", "center_value": number(total), "center_label": "productos",
            "items": [(f"{replenishment_service.PRIORITY_LABELS[p]} ({n})", None, replenishment_service.PRIORITY_TONES[p], n)
                      for p, n in counts.items()], "empty": total == 0}


# ---------------------------------------------------------------------------
# Tendencias
# ---------------------------------------------------------------------------
def trend_row(item: ProductAnalysis) -> dict:
    trend, product = item.trend, item.product
    variation = pct(trend.variation_pct, signed=True) if trend.variation_pct is not None else "Nuevo"
    return {"id": product.id, "p": product_cell(product), "cat": item.category_name,
            "sales": qty(trend.current, product), "prev": qty(trend.previous, product), "variation": variation,
            "tone": trend.tone, "badge": (trend.label, trend.tone), "cause": trend.primary_observation,
            "observations": trend.observations, "spark": trend.spark, "status": trend.status}


def trend_sort_key(state: str):
    def key(item: ProductAnalysis):
        v = item.trend.variation_pct
        magnitude = abs(v) if v is not None else float("inf")
        if state == trend_service.TREND_UP:
            return -magnitude
        if state == trend_service.TREND_DOWN:
            return v if v is not None else 0.0
        if state == trend_service.TREND_STABLE:
            return magnitude
        return -magnitude
    return key


def average_variation(items: list[ProductAnalysis]) -> float | None:
    current = sum(i.trend.current for i in items)
    previous = sum(i.trend.previous for i in items)
    return None if previous == 0 else (current - previous) / previous * 100.0


def eligibility_breakdown(analysis: Analysis) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for item in analysis.items:
        if item.forecast is None:
            counts[item.eligibility.label] = counts.get(item.eligibility.label, 0) + 1
    return sorted(counts.items(), key=lambda x: -x[1])


def tones_for_series(count: int) -> list[str]:
    palette = ["blue", "green", "orange", "purple", "red"]
    return [palette[i % len(palette)] for i in range(count)]
