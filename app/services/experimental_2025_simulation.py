"""Explicit experimental inventory scenarios. No PurchaseOrder or LIVE access."""
from collections import defaultdict
from datetime import date
from math import fsum

from .experimental_inventory_service import simulate_inventory
from . import experimental_2025_experiment as experiment
from . import experimental_2025_models as models
from .experimental_2025_temporal import normalized_name

METRICS = ("total_demand", "fulfilled_units", "unmet_units", "fill_rate", "stockout_periods",
           "cycle_service_level", "average_on_hand", "max_on_hand", "ending_stock", "order_count",
           "total_ordered_units")


def simulate_view(panel, calendar, *, view, horizon, history_panel=None, bridge=None,
                  inventory_context=None, emit=lambda row: None):
    """L=2/R=5 or L=7/R=23 periods, lost_sales, zero initial stock and buffer.

    These are declared stress scenarios, not company lead times or policies.
    Forecasts are generated before each review demand. No terminal snapshot is
    used to initialize an earlier simulation. A separate 2026 sensitivity may
    use the synthetic 2025 snapshot, explicitly dated by scenario assumption.
    """
    lead, review = (2, 5) if horizon == 7 else (7, 23)
    time_unit = "calendar_day" if view == "2025_ONLY" else "operational_day"
    history_panel, bridge = history_panel or {}, bridge or {}
    grid = experiment.origins(calendar, horizon, view)
    start_day = grid[0][0] if grid else None
    groups = defaultdict(list)
    unavailable = defaultdict(int)
    context = {row["product_code"]: row for row in (inventory_context or [])}
    for code, points in panel.items():
        if view in experiment.VIEWS[2:] and code not in bridge:
            unavailable["cross_year_identity_or_unit_unverified"] += 1
            continue
        targets = [p for p in points if start_day and p["date"] >= start_day]
        fit = experiment.training(points, start_day, view, history_panel.get(code, [])) if targets else []
        reason = experiment.eligibility(fit)
        if reason or not targets:
            unavailable[reason or "no_simulation_targets"] += 1
            continue
        for model in ("mean", "tsb", "two_part", "adida"):
            forecasts, failure = [], None
            for index, target in enumerate(targets):
                if index % review:
                    forecasts.append([0.0] * horizon)
                    continue
                local = experiment.training(points, target["date"], view, history_panel.get(code, []))
                candidate = models.forecast_candidate(model, [p["quantity"] for p in local],
                                                      [p["date"] for p in local], horizon,
                                                      time_unit=time_unit, with_uncertainty=False)
                if not candidate["available"]:
                    failure = candidate["unavailable_reason"]
                    break
                forecasts.append(candidate["forecast"])
            if failure:
                unavailable[model + ":" + failure] += 1
                continue
            scenarios = [("zero_initial_stock", 0.0)]
            snapshot = context.get(code, {})
            if view != "2025_ONLY" and code in bridge and snapshot.get("usable_for_demand_unit"):
                snapshot_day = date.fromisoformat(snapshot["snapshot_date"])
                if snapshot_day >= targets[0]["date"] or snapshot_day.year != 2025:
                    unavailable["snapshot_not_before_simulation"] += 1
                elif snapshot.get("demand_identity") and normalized_name(snapshot.get("name", "")) != normalized_name(snapshot["demand_identity"]["name"]):
                    unavailable["snapshot_identity_unverified"] += 1
                else:
                    scenarios.append(("synthetic_2025_snapshot_initial_stock", snapshot["stock"]))
            for scenario_id, stock in scenarios:
                simulation = simulate_inventory(demand_series=[p["quantity"] for p in targets],
                                                forecast_series=forecasts, initial_stock=stock,
                                                lead_time_periods=lead, review_period=review,
                                                stockout_mode="lost_sales", policy="forecast_order_up_to",
                                                policy_parameters={"target_stock": 0.0})
                if any(abs(value) > 1e-7 for value in simulation["accounting"].values()):
                    raise ValueError("Inventory accounting did not reconcile")
                row = {"view": view, "horizon": horizon, "product_code": code, "model": model,
                       "scenario_id": scenario_id, "time_unit": time_unit,
                       "start": targets[0]["date"].isoformat(), "end": targets[-1]["date"].isoformat(),
                       "scenario": simulation["scenario"], "metrics": simulation["metrics"],
                       "accounting": simulation["accounting"],
                       "initial_stock_data_kind": "synthetic_structured" if scenario_id != "zero_initial_stock" else "scenario_constant"}
                groups[scenario_id + ":" + model].append(row)
                emit(row)
    result = {}
    for key, rows in groups.items():
        summaries = {}
        for metric in METRICS:
            values = [r["metrics"][metric] for r in rows if r["metrics"][metric] is not None]
            summaries[metric] = fsum(values) / len(values) if values else None
        total = fsum(r["metrics"]["total_demand"] for r in rows)
        fulfilled = fsum(r["metrics"]["fulfilled_units"] for r in rows)
        result[key] = {"sku_count": len(rows), "macro_metrics": summaries,
                       "quantity_index_total_demand": total,
                       "quantity_index_fulfilled_units": fulfilled,
                       "quantity_index_fill_rate": fulfilled / total if total else None}
    return {"scenario": {"lead_time_periods": lead, "review_period": review,
                         "stockout_mode": "lost_sales", "policy": "forecast_order_up_to",
                         "target_stock": 0, "time_unit": time_unit,
                         "parameters_are_enterprise_estimates": False},
            "results": result, "unavailable_reasons": dict(unavailable),
            "macro_definition": "unweighted_mean_per_sku;quantities_not_homogeneous_catalog_total"}
