"""Orquestación del análisis predictivo por producto (solo lectura).

    series (fuente canónica) -> elegibilidad -> backtest -> pronóstico -> tendencia
    -> riesgo (stock + supply en camino) -> recomendación de reabastecimiento

Devuelve estructuras de dominio; el controlador filtra, pagina y formatea. No escribe en
la BD ni crea órdenes. Sin historial suficiente cada bloque queda en ``None`` y la UI lo
declara como tal (nunca se rellena con fixtures).

Database inputs are materialized before request-owned connections are released.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import joinedload, load_only

from app.models import Product, Category
from app.models.purchase import PurchaseOrder, PurchaseOrderLine, Supplier
from app.extensions import db
from app.services import backtest_service, critical_product_service as risk_service, forecast_service
from app.services import predictive_data_service as data, replenishment_service, trend_service
from app.services import experimental_demand_service
from app.services.demand_analysis_service import institutional_today
from app.services.inventory_service import COMMERCIAL_ZONE
from app.services.predictive_read_service import NamedValue, ProductInput, materialized_read, product_input


def product_options():
    return (load_only(Product.id, Product.code, Product.name, Product.category_id, Product.unit,
                      Product.current_stock, Product.minimum_stock, Product.purchase_price,
                      Product.image_url, Product.is_active, Product.approval_status, Product.created_at),
            joinedload(Product.category).load_only(Category.id, Category.name))


@dataclass
class ProductAnalysis:
    product: Product | ProductInput
    series: data.TimeSeries
    eligibility: data.Eligibility
    forecast: forecast_service.Forecast | None
    evaluation: backtest_service.ModelEvaluation | None
    trend: trend_service.TrendResult | None
    risk: risk_service.RiskAssessment
    recommendation: replenishment_service.Recommendation | None
    pending_supply: int
    supplier_ids: frozenset[int] = frozenset()
    experimental: dict | None = None
    forecast_status: str = "unavailable"
    forecast_reason: str | None = None

    @property
    def backtest(self) -> backtest_service.BacktestResult | None:
        if self.evaluation is None or self.forecast is None or self.forecast.kind != "model":
            return None
        return self.evaluation.results.get(self.forecast.model)

    @property
    def category_name(self) -> str:
        return self.product.category.name if self.product.category else "Sin categoría"


@dataclass
class Analysis:
    horizon_days: int
    model: str
    granularity: str
    today: date
    generated_at: datetime
    items: list[ProductAnalysis] = field(default_factory=list)
    suppliers: list[NamedValue] = field(default_factory=list)
    categories: list[NamedValue] = field(default_factory=list)
    computation_status: str = "ready"
    computation_token: str | None = None

    @property
    def as_of(self) -> date:
        """Último período observado completo: ancla de tendencias y ventanas (hoy es parcial)."""
        return data.last_complete_period(self.today, self.granularity)

    @property
    def with_forecast(self) -> list[ProductAnalysis]:
        return [i for i in self.items if i.forecast is not None]

    @property
    def with_model(self) -> list[ProductAnalysis]:
        return [i for i in self.items if i.forecast is not None and i.forecast.kind == "model"]

    @property
    def evaluated(self) -> list[ProductAnalysis]:
        return [i for i in self.items if i.backtest is not None and i.backtest.folds]

    @property
    def history_start(self) -> date | None:
        starts = [i.series.starts[0] for i in self.items if i.series.starts and i.series.positive_periods]
        return min(starts) if starts else None

    def models_used(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.with_model:
            counts[item.forecast.model] = counts.get(item.forecast.model, 0) + 1
        return counts


def _suppliers_by_product() -> dict[int, set[int]]:
    rows = (db.session.query(PurchaseOrderLine.product_id, PurchaseOrder.supplier_id)
            .join(PurchaseOrder, PurchaseOrderLine.purchase_order_id == PurchaseOrder.id)
            .filter(PurchaseOrder.status != "cancelled").distinct().all())
    result: dict[int, set[int]] = {}
    for product_id, supplier_id in rows:
        result.setdefault(int(product_id), set()).add(int(supplier_id))
    return result


def _tables_ready() -> bool:
    from app.utils.schema import table_ready

    return all(table_ready(name) for name in ("suppliers", "purchase_orders", "purchase_order_lines"))


_NOT_PROVIDED = object()


def product_eligibility(product, series):
    approval = getattr(product, "approval_status", "approved")
    if approval != "approved":
        reason = "Producto pendiente de aprobación." if approval == "pending" else "Producto no aprobado."
        return data.Eligibility(data.INACTIVE, reason, False)
    return data.classify_eligibility(series, bool(product.is_active))


def forecast_availability(product, series, eligibility, forecast, today, computation_status, observed_only):
    """Causas verificadas en el análisis actual; no infiere fallos ni ausencia de ventas de un cero."""
    if forecast is not None:
        return "available", None
    if not eligibility.descriptive_allowed and eligibility.status != data.ELIGIBLE:
        if eligibility.status == data.NO_DEMAND and getattr(product, "created_at", None):
            age = (today - product.created_at.date()).days
            if 0 <= age < data.ELIGIBILITY_CONFIG[series.granularity].min_periods * series.period_days:
                age_label = "1 día" if age == 1 else f"{age} días"
                return "new_without_history", f"Producto creado hace {age_label}. {eligibility.reason}"
        return eligibility.status.lower(), eligibility.reason
    pending_reasons = {
        "pending": "El pronóstico está en cola de cálculo.",
        "running": "El pronóstico se está calculando.",
        "busy": "El cálculo está ocupado; todavía no hay un pronóstico disponible.",
        "error": "El cálculo del pronóstico no se pudo completar.",
        "expired": "El seguimiento del cálculo venció; actualice el análisis.",
        "unavailable": "El método seleccionado no produjo un pronóstico válido.",
    }
    if computation_status in pending_reasons:
        return computation_status, pending_reasons[computation_status]
    if observed_only:
        return "not_generated", "El análisis solicitado solo incluye datos observados; no se generó pronóstico."
    return "model_unavailable", "El método seleccionado no produjo un pronóstico válido con esta historia."


def prediction_block(series, horizon_days, model, *, predictor=None):
    """The original LIVE evaluation/selection, without inventory or database state."""
    horizon_periods = forecast_service.periods_for(horizon_days, series.granularity)
    periods = horizon_periods[0] + (1 if horizon_periods[1] else 0)
    min_train = data.ELIGIBILITY_CONFIG[series.granularity].min_periods
    candidates = None if model == forecast_service.AUTO_MODEL else (model,)
    evaluation = backtest_service.evaluate_models(series, periods, min_train, candidates=candidates,
                                                  predictor=predictor)
    return _forecast_available(series, horizon_days, model, evaluation, predictor=predictor), evaluation


def analyze_product(product: Product, series: data.TimeSeries, horizon_days: int, model: str, today: date,
                    pending_supply: int = 0, *, observed_only: bool = False,
                    trend_config: trend_service.TrendConfig = trend_service.DEFAULT_CONFIG,
                    predictor=None, prediction=_NOT_PROVIDED, computation_status="ready") -> ProductAnalysis:
    # LIVE policy is intentionally separate from empirical train-only evaluation.
    if series.time_unit != "calendar_day":
        raise ValueError("Use empirical_backtest_service for operational-day evaluation")
    eligibility = product_eligibility(product, series)
    evaluation = None
    forecast = None
    if not observed_only and eligibility.status == data.ELIGIBLE:
        forecast, evaluation = (prediction_block(series, horizon_days, model, predictor=predictor)
                                if prediction is _NOT_PROVIDED else prediction)
    elif not observed_only and eligibility.descriptive_allowed:
        forecast = forecast_service.descriptive_rate(series, horizon_days)
    if forecast is not None and not usable_forecast(forecast, horizon_days, series.granularity):
        forecast = None
        computation_status = "unavailable"
    forecast_status, forecast_reason = forecast_availability(
        product, series, eligibility, forecast, today, computation_status, observed_only)
    as_of = data.last_complete_period(today, series.granularity)
    trend = trend_service.analyze(series, as_of, trend_config) if series.positive_periods else None
    risk = risk_service.assess(product.current_stock, product.minimum_stock,
                               forecast.daily_rate if forecast else None, forecast is not None,
                               trend_up=bool(trend and trend.status == trend_service.TREND_UP),
                               config=replace(risk_service.DEFAULT_CONFIG, horizon_days=horizon_days))
    recommendation = None
    if forecast is not None and not (series.is_service or getattr(product, "is_service", False)):
        recommendation = replenishment_service.compute(int(product.current_stock), int(product.minimum_stock),
                                                       forecast.daily_rate, pending_supply,
                                                       Decimal(product.purchase_price or 0), today,
                                                       rate_unit=forecast.rate_unit)
    # Separate experimental eligible/intermittent route. Never feeds stock or orders.
    experimental = experimental_demand_service.analyze(series, horizon_days, eligibility)
    return ProductAnalysis(product, series, eligibility, forecast, evaluation, trend, risk, recommendation,
                           pending_supply, experimental=experimental,
                           forecast_status=forecast_status, forecast_reason=forecast_reason)


def usable_forecast(forecast, horizon_days, granularity="daily"):
    total = risk_service.nonnegative_number(forecast.total)
    daily = risk_service.nonnegative_number(forecast.daily_rate)
    whole, fraction = forecast_service.periods_for(horizon_days, granularity)
    return (forecast.horizon_days == horizon_days and forecast.rate_unit == "calendar_day"
            and total is not None and daily is not None and len(forecast.path) == whole + bool(fraction)
            and all(risk_service.nonnegative_number(v) is not None for v in forecast.path)
            and (total > 0) == (daily > 0)
            and abs(daily - total / horizon_days) <= 1e-9)


def _forecast_available(series, horizon_days, model, evaluation, *, predictor=None):
    """Método explícito indisponible -> None; auto intenta ranking válido y luego baseline."""
    choices = [model]
    if model == forecast_service.AUTO_MODEL:
        choices = [name for name, wape in evaluation.ranking() if wape is not None] if evaluation else []
        if forecast_service.BASELINE_MODEL not in choices:
            choices.append(forecast_service.BASELINE_MODEL)
    for chosen in choices:
        result = evaluation.results.get(chosen) if evaluation else None
        try:
            return forecast_service.forecast(series, horizon_days, chosen,
                                             residual_std=result.residual_std if result else None,
                                             predictor=predictor)
        except forecast_service.ModelUnavailable:
            continue
    return None


def analyze(horizon_days: int = forecast_service.DEFAULT_HORIZON_DAYS, model: str = forecast_service.AUTO_MODEL,
            granularity: str = data.GRANULARITY_DAILY, today: date | None = None, *,
            release_session: bool = False, observed_only: bool = False,
            trend_config: trend_service.TrendConfig = trend_service.DEFAULT_CONFIG,
            cache_predictions: bool = False, defer_predictions: bool = False) -> Analysis:
    if horizon_days not in forecast_service.HORIZONS_DAYS:
        raise ValueError(f"Horizonte no soportado: {horizon_days}")
    if model != forecast_service.AUTO_MODEL and model not in forecast_service.MODELS:
        raise ValueError(f"Modelo no soportado: {model}")
    today = today or institutional_today()
    analysis = Analysis(horizon_days, model, granularity, today, datetime.now(COMMERCIAL_ZONE))
    with materialized_read(release_session=release_session):
        series_map = data.load_series(granularity, today)
        purchases_ready = _tables_ready()
        pending = replenishment_service.pending_supply_by_product() if purchases_ready and not observed_only else {}
        suppliers_map = _suppliers_by_product() if purchases_ready else {}
        products = [product_input(p) for p in Product.query.options(*product_options())
                    .order_by(Product.name, Product.id).all()]
        if purchases_ready:
            analysis.suppliers = [NamedValue(s.id, s.name) for s in Supplier.query.with_entities(Supplier.id, Supplier.name)
                                  .filter(Supplier.is_active.is_(True)).order_by(Supplier.name).all()]
        analysis.categories = [NamedValue(c.id, c.name) for c in Category.query.with_entities(Category.id, Category.name)
                               .filter(Category.is_active.is_(True)).order_by(Category.name).all()]
    from app.services import predictive_cache_service

    blocks = {}
    if defer_predictions and not observed_only:
        from app.services import predictive_job_service

        specs = [(series_map[p.id], horizon_days, model) for p in products
                 if p.id in series_map and product_eligibility(p, series_map[p.id]).status == data.ELIGIBLE]
        analysis.computation_token = predictive_job_service.prediction_token(specs)
        analysis.computation_status, blocks = predictive_job_service.request_predictions(
            specs, token=analysis.computation_token)
    for product in products:
        series = series_map.get(product.id) or data.aggregate_daily(product.id, [], granularity, end=analysis.as_of)
        item = analyze_product(product, series, horizon_days, model, today, pending.get(product.id, 0),
                               observed_only=observed_only or analysis.computation_status != "ready", trend_config=trend_config,
                               predictor=predictive_cache_service.predictor(product.id)
                               if cache_predictions and not observed_only and not defer_predictions else None,
                               prediction=blocks.get((product.id, horizon_days), _NOT_PROVIDED),
                               computation_status=analysis.computation_status)
        item.supplier_ids = frozenset(suppliers_map.get(product.id, ()))
        analysis.items.append(item)
    return analysis
