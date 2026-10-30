"""Read-only detail DTO: compose the existing predictive engine, never a second engine."""
from dataclasses import asdict
from datetime import timedelta

from sqlalchemy.orm import joinedload

from app.models import Product, Sale, SaleLine, StockMovement
from app.models.purchase import ProductSupplier, PurchaseOrder, PurchaseOrderLine, Supplier, STATUS_LABELS
from app.services import predictive_analysis_service as analysis, predictive_data_service as data
from app.services import critical_product_service, forecast_service, replenishment_service
from app.services.demand_analysis_service import institutional_today
from app.services.demand_data_service import commercial_date, utc_naive_start_of_commercial_day
from app.utils.schema import table_ready
from app.services.predictive_read_service import NamedValue, materialized_read, product_input


def _orders(product_id):
    if not analysis._tables_ready():
        return []
    lines = (PurchaseOrderLine.query.join(PurchaseOrder)
             .options(joinedload(PurchaseOrderLine.order).joinedload(PurchaseOrder.supplier))
             .filter(PurchaseOrderLine.product_id == product_id,
                     PurchaseOrder.status.in_(replenishment_service.SUPPLY_STATUSES),
                     PurchaseOrderLine.quantity_ordered > PurchaseOrderLine.quantity_received)
             .order_by(PurchaseOrder.ordered_at.desc()).all())
    return [{"number": line.order.order_number, "supplier": line.order.supplier.name,
             "status": line.order.status, "status_label": STATUS_LABELS[line.order.status],
             "pending": line.quantity_ordered - line.quantity_received,
             "expected": line.order.estimated_delivery_date.isoformat() if line.order.estimated_delivery_date else None}
            for line in lines]


def _sales(product_id, today):
    if not table_ready("sales") or not table_ready("sale_lines"):
        return []
    start = utc_naive_start_of_commercial_day(today - timedelta(days=90))
    end = utc_naive_start_of_commercial_day(today + timedelta(days=1))
    rows = (SaleLine.query.join(Sale).options(joinedload(SaleLine.sale).joinedload(Sale.customer))
            .filter(SaleLine.product_id == product_id, Sale.status == "completed", Sale.sold_at >= start,
                    Sale.sold_at < end).order_by(Sale.sold_at.desc(), SaleLine.id.desc()).limit(10).all())
    return [{"date": commercial_date(line.sale.sold_at).isoformat(), "document": line.sale.sale_number,
             "customer": line.sale.customer.name, "quantity": line.quantity} for line in rows]


def _movements(product_id, today):
    start = utc_naive_start_of_commercial_day(today - timedelta(days=29))
    end = utc_naive_start_of_commercial_day(today + timedelta(days=1))
    rows = (StockMovement.query.filter(StockMovement.product_id == product_id,
                                      StockMovement.created_at >= start, StockMovement.created_at < end)
            .order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).limit(10).all())
    labels = {"entrada": "Entrada", "salida": "Salida", "ajuste": "Ajuste", "entry": "Entrada", "exit": "Salida"}
    return [{"date": commercial_date(row.created_at).isoformat(), "type": labels.get(row.movement_type, row.movement_type),
             "document": None, "entry": row.quantity if row.movement_type in ("entrada", "entry") else max(0, row.new_stock - row.previous_stock) if row.movement_type == "ajuste" else 0,
             "exit": row.quantity if row.movement_type in ("salida", "exit") else max(0, row.previous_stock - row.new_stock) if row.movement_type == "ajuste" else 0, "stock": row.new_stock} for row in rows]


def build(product_id, *, can_sales=False, can_inventory=False, can_buy=False, can_purchases=False,
          release_session=False, cache_predictions=False, defer_predictions=False):
    today = institutional_today()
    with materialized_read(release_session=release_session):
        row = Product.query.options(*analysis.product_options()).filter_by(id=product_id).first()
        if row is None:
            return None
        product = product_input(row)
        orders = _orders(product_id)
        # Keep canonical pending/partial supply semantics; only materialize before CPU.
        pending = replenishment_service.pending_supply_by_product().get(product_id, 0) if analysis._tables_ready() else 0
        series = data.load_series(today=today, product_id=product_id).get(product_id) or data.aggregate_daily(
            product_id, [], "daily", end=today - timedelta(days=1))
        suppliers = [NamedValue(s.id, s.name) for s in Supplier.query.with_entities(Supplier.id, Supplier.name)
                     .filter(Supplier.is_active.is_(True)).order_by(Supplier.name).all()] if can_buy and analysis._tables_ready() else []
        supplier_names = []
        preferred_supplier = None
        if table_ready("product_suppliers") and analysis._tables_ready():
            link = (ProductSupplier.query.options(joinedload(ProductSupplier.supplier))
                    .join(Supplier, ProductSupplier.supplier_id == Supplier.id)
                    .filter(ProductSupplier.product_id == product_id,
                            ProductSupplier.is_preferred.is_(True),
                            ProductSupplier.is_active.is_(True),
                            Supplier.is_active.is_(True)).first())
            if link is not None:
                preferred_supplier = {"id": link.supplier_id, "name": link.supplier.name,
                                      "purchase_price": f"{link.purchase_price:.2f}",
                                      "purchase_price_currency": link.purchase_price_currency,
                                      "lead_time_days": link.lead_time_days}
        if can_purchases and analysis._tables_ready():
            supplier_names = [name for (name,) in (Supplier.query.with_entities(Supplier.name)
                .join(PurchaseOrder, PurchaseOrder.supplier_id == Supplier.id)
                .join(PurchaseOrderLine, PurchaseOrderLine.purchase_order_id == PurchaseOrder.id)
                .filter(PurchaseOrderLine.product_id == product_id, PurchaseOrder.status != "cancelled")
                .distinct().order_by(Supplier.name).all())]
        sales = _sales(product_id, today) if can_sales else []
        movements = _movements(product_id, today) if can_inventory else []
    from app.services import predictive_cache_service

    status, blocks, token = "ready", {}, None
    if defer_predictions:
        from app.services import predictive_job_service

        specs = [(series, h, forecast_service.AUTO_MODEL) for h in forecast_service.HORIZONS_DAYS
                 if analysis.product_eligibility(product, series).status == data.ELIGIBLE]
        token = predictive_job_service.prediction_token(specs)
        status, blocks = predictive_job_service.request_predictions(specs, token=token)
    predictor = predictive_cache_service.predictor(product_id) if cache_predictions and not defer_predictions else None
    items = {h: analysis.analyze_product(product, series, h, forecast_service.AUTO_MODEL, today, pending,
                                        predictor=predictor, observed_only=status != "ready",
                                        prediction=blocks.get((product_id, h), analysis._NOT_PROVIDED),
                                        computation_status=status)
             for h in forecast_service.HORIZONS_DAYS}
    item = items[30]
    observed = [(d.isoformat(), v) for d, v in zip(series.starts, series.values)
                if today - timedelta(days=90) <= d < today]
    values = [v for _, v in observed] if series.event_count else []
    periods = []
    for horizon, result in items.items():
        fc = result.forecast
        periods.append({"days": horizon, "start": today.isoformat(), "end": (today + timedelta(days=horizon - 1)).isoformat(),
                        "total": fc.total if fc else None, "daily_rate": fc.daily_rate if fc else None, "interval": fc.half_width if fc else None,
                        "path": fc.path if fc else [], "model": fc.model_label if fc else None,
                        "forecast_status": result.forecast_status, "forecast_reason": result.forecast_reason,
                        "two_part": result.experimental["two_part"],
                        "uncertainty": result.experimental["uncertainty"],
                        "prediction_policy": result.experimental["prediction_policy"]})
    rec = asdict(item.recommendation) if item.recommendation else None
    if rec:
        rec["unit_cost"] = str(rec["unit_cost"])
        rec["unit_cost_currency"] = getattr(product, "purchase_price_currency", None)
        rec["cost_source"] = "product_reference"
        rec["recommended_date"] = rec["recommended_date"].isoformat()
    backtest = item.backtest
    return {"reference": False, "today": today.isoformat(), "computation_status": status,
            "computation_token": token,
            "product": {"id": product.id, "name": product.name, "sku": product.code, "unit": product.unit or "uds.",
                        "image": product.image_url, "category": item.category_name,
                        "suppliers": supplier_names,
                        "stock": product.current_stock, "minimum": product.minimum_stock},
            "risk": {"level": item.risk.level, "cover": item.risk.days_of_cover,
                     "cover_label": critical_product_service.cover_label(item.risk.days_of_cover, item.forecast is not None,
                                                                          zero_demand=item.forecast is not None and item.forecast.total == 0),
                     "reasons": list(item.risk.reasons), "reorder_point": item.risk.reorder_point},
            "history": {"dates": [d for d, _ in observed] if values else [], "values": values,
                        "total": sum(values) if values else None, "average": sum(values) / len(values) if values else None,
                        "maximum": max(values) if values else None, "minimum": min(values) if values else None,
                        "source": series.data_source},
            "periods": periods, "forecast_kind": item.forecast.kind if item.forecast else None,
            "demand_profile": item.experimental["demand_profile"],
            "wape": backtest.metrics.wape if backtest and backtest.folds else None,
            "trend": {"label": item.trend.label, "variation": item.trend.variation_pct} if item.trend else None,
            "eligibility": item.eligibility.reason, "recommendation": rec, "pending": pending,
            "orders": orders if can_purchases else [], "sales": sales,
            "movements": movements,
            "permissions": {"sales": can_sales, "inventory": can_inventory, "purchases": can_purchases},
            "can_order": bool(can_buy and suppliers and product.is_active and rec and rec["suggested_quantity"] > 0),
            "preferred_supplier": preferred_supplier,
            "suppliers": [{"id": s.id, "name": s.name} for s in suppliers]}
