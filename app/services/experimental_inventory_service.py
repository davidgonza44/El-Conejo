"""Pure inventory scenarios; no application, database or purchase dependencies.

Forecast rows belong to their origin: row t is available before demand at t.
Callers must establish that provenance; future realized demand is never an
input to the order decision. All scenario values must be supplied explicitly.
"""

from collections.abc import Mapping, Sequence
import math
from numbers import Real


def _finite(value, label):
    if not math.isfinite(value):
        raise ValueError(f"{label} must remain finite")
    return value


def _nonnegative(value, label):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a finite nonnegative number")
    try:
        result = _finite(float(value), label)
    except OverflowError as error:
        raise ValueError(f"{label} exceeds finite arithmetic") from error
    if result < 0:
        raise ValueError(f"{label} must be nonnegative")
    return result


def _sum(values, label):
    try:
        result = math.fsum(values)
    except OverflowError as error:
        raise ValueError(f"{label} exceeds finite arithmetic") from error
    return _finite(result, label)


def _series(values, label):
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{label} must be an explicit sequence")
    return [_nonnegative(value, label) for value in values]


def _validate_periods(lead_time_periods, review_period):
    for label, value, minimum in (
        ("lead_time_periods", lead_time_periods, 0),
        ("review_period", review_period, 1),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{label} must be an integer >= {minimum}")


def _validate_forecasts(forecast_series, periods, policy, horizon):
    if isinstance(forecast_series, (str, bytes)) or not isinstance(forecast_series, Sequence):
        raise ValueError("forecast_series must be a matrix indexed by origin")
    forecasts = [_series(row, "forecast_series row") for row in forecast_series]
    if len(forecasts) != periods:
        raise ValueError("one forecast row is required per simulated period")
    if policy == "forecast_order_up_to" and any(len(row) != horizon for row in forecasts):
        raise ValueError("each forecast row requires lead_time_periods + review_period values")
    return forecasts


def _validate_scenario(demand_series, forecast_series, initial_stock,
                       lead_time_periods, review_period, stockout_mode,
                       policy, policy_parameters):
    demand = _series(demand_series, "demand_series")
    if not demand:
        raise ValueError("demand_series must contain at least one period")
    _validate_periods(lead_time_periods, review_period)
    if stockout_mode not in ("lost_sales", "backorder"):
        raise ValueError("stockout_mode must be lost_sales or backorder")
    if policy not in ("fixed_order_up_to", "forecast_order_up_to"):
        raise ValueError("an explicit supported policy is required")
    if not isinstance(policy_parameters, Mapping) or set(policy_parameters) != {"target_stock"}:
        raise ValueError("policy_parameters requires exactly target_stock")
    target_stock = _nonnegative(policy_parameters["target_stock"], "target_stock")
    stock = _nonnegative(initial_stock, "initial_stock")
    forecasts = _validate_forecasts(forecast_series, len(demand), policy,
                                    lead_time_periods + review_period)
    return demand, forecasts, stock, target_stock


def _serve_backorder(state):
    served = min(state["on_hand"], state["backorder"])
    state["on_hand"] -= served
    state["backorder"] -= served
    return served


def _review_order(state, period, forecast_row, lead_time, review_period,
                  policy, target_stock):
    result = {
        "review": period % review_period == 0,
        "forecast_used": None,
        "inventory_position_before_order": None,
        "order_target": None,
        "ordered_units": 0.0,
        "order_arrival_period": None,
        "immediate_receipts": 0.0,
    }
    if not result["review"]:
        return result
    position = _sum(
        [state["on_hand"], -state["backorder"], *state["pipeline"].values()],
        "inventory position",
    )
    target = target_stock
    if policy == "forecast_order_up_to":
        result["forecast_used"] = list(forecast_row)
        target = _sum([target_stock, *forecast_row], "forecast target")
    ordered = max(0.0, _finite(target - position, "order quantity"))
    result.update(inventory_position_before_order=position,
                  order_target=target, ordered_units=ordered)
    if ordered > 0:
        arrival = period + lead_time
        result["order_arrival_period"] = arrival
        if lead_time == 0:
            result["immediate_receipts"] = ordered
            state["on_hand"] = _sum([state["on_hand"], ordered], "on hand")
        else:
            state["pipeline"][arrival] = _sum(
                [state["pipeline"].get(arrival, 0.0), ordered], "pipeline",
            )
    return result


def _run_period(state, period, demand, forecast_row, lead_time, review_period,
                stockout_mode, policy, target_stock):
    opening_stock = state["on_hand"]
    opening_backorder = state["backorder"]
    receipts = state["pipeline"].pop(period, 0.0)
    state["on_hand"] = _sum([opening_stock, receipts], "on hand")
    peak = state["on_hand"]
    deferred_served = _serve_backorder(state)
    decision = _review_order(state, period, forecast_row, lead_time,
                             review_period, policy, target_stock)
    peak = max(peak, state["on_hand"])
    deferred_served = _sum([deferred_served, _serve_backorder(state)], "deferred service")
    immediate_served = min(state["on_hand"], demand)
    state["on_hand"] -= immediate_served
    immediate_unmet = demand - immediate_served
    lost_units = immediate_unmet if stockout_mode == "lost_sales" else 0.0
    if stockout_mode == "backorder":
        state["backorder"] = _sum([state["backorder"], immediate_unmet], "backorder")
    return {
        "period": period,
        "opening_stock": opening_stock,
        "opening_backorder": opening_backorder,
        "scheduled_receipts": receipts,
        **decision,
        "demand": demand,
        "immediate_fulfilled_units": immediate_served,
        "backorder_fulfilled_units": deferred_served,
        "fulfilled_units": _sum([immediate_served, deferred_served], "service"),
        "immediate_unmet_units": immediate_unmet,
        "lost_units": lost_units,
        "ending_stock": state["on_hand"],
        "ending_backorder": state["backorder"],
        "pipeline_units": _sum(state["pipeline"].values(), "pipeline"),
        "peak_on_hand": peak,
    }


def _availability(available, code=None, message=None):
    reason = None if available else {"code": code, "message": message}
    return {"available": available, "reason": reason}


def _cycle_summary(trace, review_period):
    complete = len(trace) // review_period
    successes = 0
    for start in range(0, complete * review_period, review_period):
        cycle = trace[start:start + review_period]
        if cycle[0]["opening_backorder"] == 0 and all(
            row["immediate_unmet_units"] == 0 for row in cycle
        ):
            successes += 1
    return {
        "complete_review_cycles": complete,
        "successful_review_cycles": successes,
        "excluded_partial_cycle_periods": len(trace) % review_period,
        "cycle_service_level": successes / complete if complete else None,
    }


def _metrics(trace, initial_stock, review_period, stockout_mode):
    def total(field):
        return _sum((row[field] for row in trace), field)

    total_demand = total("demand")
    fulfilled = total("fulfilled_units")
    immediate = total("immediate_fulfilled_units")
    lost = total("lost_units")
    received = _sum([total("scheduled_receipts"), total("immediate_receipts")], "receipts")
    cycles = _cycle_summary(trace, review_period)
    final = trace[-1]
    metrics = {
        "total_demand": total_demand,
        "fulfilled_units": fulfilled,
        "unmet_units": lost if stockout_mode == "lost_sales" else final["ending_backorder"],
        "immediately_fulfilled_units": immediate,
        "immediate_unmet_units": total("immediate_unmet_units"),
        "backorder_fulfilled_units": total("backorder_fulfilled_units"),
        "lost_units": lost,
        "fill_rate": immediate / total_demand if total_demand else None,
        "stockout_periods": sum(row["immediate_unmet_units"] > 0 for row in trace),
        "average_on_hand": total("ending_stock") / len(trace),
        "max_on_hand": max(initial_stock, *(row["peak_on_hand"] for row in trace)),
        "ending_stock": final["ending_stock"],
        "ending_backorder": final["ending_backorder"],
        "ending_pipeline_units": final["pipeline_units"],
        "order_count": sum(row["ordered_units"] > 0 for row in trace),
        "total_ordered_units": total("ordered_units"),
        "received_units": received,
        **cycles,
    }
    if stockout_mode == "backorder":
        metrics["average_backorder"] = total("ending_backorder") / len(trace)
        metrics["max_backorder"] = max(row["ending_backorder"] for row in trace)
    accounting = {
        "stock_balance_error": _sum(
            [initial_stock, received, -fulfilled, -final["ending_stock"]], "stock balance",
        ),
        "demand_balance_error": _sum(
            [total_demand, -fulfilled, -lost, -final["ending_backorder"]], "demand balance",
        ),
        "pipeline_balance_error": _sum(
            [metrics["total_ordered_units"], -received, -final["pipeline_units"]], "pipeline balance",
        ),
    }
    availability = {
        "fill_rate": _availability(
            total_demand > 0, "zero_total_demand", "La demanda total es cero.",
        ),
        "cycle_service_level": _availability(
            cycles["complete_review_cycles"] > 0,
            "no_complete_review_cycle", "No hay ciclos completos de revisión.",
        ),
    }
    return metrics, accounting, availability


def simulate_inventory(*, demand_series, forecast_series, initial_stock,
                       lead_time_periods, review_period, stockout_mode,
                       policy, policy_parameters):
    """Run an explicit, synthetic or audited scenario without side effects.

    Policies: fixed_order_up_to uses target_stock as fixed target;
    forecast_order_up_to uses sum(row_t) + target_stock, where target_stock
    is an explicit buffer and len(row_t) = lead_time_periods + review_period.
    Quantity = max(0, target - on_hand - pipeline + backorder).
    """
    demand, forecasts, stock, target = _validate_scenario(
        demand_series, forecast_series, initial_stock, lead_time_periods,
        review_period, stockout_mode, policy, policy_parameters,
    )
    state = {"on_hand": stock, "backorder": 0.0, "pipeline": {}}
    trace = [
        _run_period(state, period, units, forecasts[period], lead_time_periods,
                    review_period, stockout_mode, policy, target)
        for period, units in enumerate(demand)
    ]
    metrics, accounting, availability = _metrics(trace, stock, review_period, stockout_mode)
    return {
        "experimental": True,
        "scenario": {
            "periods": len(demand),
            "initial_stock": stock,
            "lead_time_periods": lead_time_periods,
            "review_period": review_period,
            "stockout_mode": stockout_mode,
            "policy": policy,
            "policy_parameters": {"target_stock": target},
        },
        "metrics": metrics,
        "metric_availability": availability,
        "accounting": accounting,
        "trace": trace,
        "ending_orders": [
            {"arrival_period": period, "units": units}
            for period, units in sorted(state["pipeline"].items())
        ],
    }
