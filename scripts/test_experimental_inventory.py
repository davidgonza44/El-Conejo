"""Hand-calculable synthetic inventory tests; never import the Flask app."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "app/services/experimental_inventory_service.py"
SPEC = importlib.util.spec_from_file_location("experimental_inventory_fixture", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
simulate_inventory = MODULE.simulate_inventory


def demo_fixed(demand, *, stock, target, lead=1, review=1, mode="lost_sales"):
    """Defaults are DEMO fixture values, never enterprise observations."""
    return {
        "demand_series": demand,
        "forecast_series": [[] for _ in demand],
        "initial_stock": stock,
        "lead_time_periods": lead,
        "review_period": review,
        "stockout_mode": mode,
        "policy": "fixed_order_up_to",
        "policy_parameters": {"target_stock": target},
    }


def demo_forecast(demand, *, stock, forecasts, lead=1, review=1, buffer=0, mode="lost_sales"):
    """Every numeric default belongs exclusively to a synthetic fixture."""
    scenario = demo_fixed(demand, stock=stock, target=buffer, lead=lead, review=review, mode=mode)
    scenario["forecast_series"] = forecasts
    scenario["policy"] = "forecast_order_up_to"
    return scenario


class ExperimentalInventoryTests(unittest.TestCase):
    def test_zero_demand_has_unavailable_fill_rate_and_no_orders(self):
        result = simulate_inventory(**demo_fixed([0, 0, 0], stock=5, target=5))
        metrics = result["metrics"]
        for field in ("total_demand", "fulfilled_units", "unmet_units", "order_count"):
            self.assertEqual(metrics[field], 0)
        self.assertIsNone(metrics["fill_rate"])
        self.assertEqual(result["metric_availability"]["fill_rate"]["reason"]["code"], "zero_total_demand")
        for field in ("average_on_hand", "max_on_hand", "ending_stock"):
            self.assertEqual(metrics[field], 5)
        self.assertEqual(metrics["cycle_service_level"], 1)
        json.dumps(result, allow_nan=False)

    def test_constant_demand_forecast_scenario_computed_by_hand(self):
        result = simulate_inventory(**demo_forecast([2, 2, 2], stock=2, forecasts=[[2, 2]] * 3))
        expected = {
            "total_demand": 6, "fulfilled_units": 6, "unmet_units": 0,
            "stockout_periods": 0, "fill_rate": 1, "cycle_service_level": 1,
            "order_count": 3, "total_ordered_units": 6, "received_units": 4,
            "average_on_hand": 0, "ending_stock": 0, "max_on_hand": 2,
        }
        for field, value in expected.items():
            self.assertEqual(result["metrics"][field], value, field)
        self.assertEqual(result["ending_orders"], [{"arrival_period": 3, "units": 2}])

    def test_sufficient_initial_stock_serves_without_replenishment(self):
        result = simulate_inventory(**demo_fixed([2, 0, 3], stock=10, target=0))
        expected = {"fulfilled_units": 5, "ending_stock": 5, "average_on_hand": 7,
                    "max_on_hand": 10, "order_count": 0}
        for field, value in expected.items():
            self.assertEqual(result["metrics"][field], value, field)

    def test_stockout_lost_sales_is_not_fulfilled_by_later_receipt(self):
        result = simulate_inventory(**demo_forecast([3, 3, 3], stock=0, forecasts=[[3, 3]] * 3))
        expected = {"fulfilled_units": 6, "unmet_units": 3, "lost_units": 3,
                    "stockout_periods": 1, "order_count": 2, "total_ordered_units": 9,
                    "ending_backorder": 0}
        for field, value in expected.items():
            self.assertEqual(result["metrics"][field], value, field)
        self.assertAlmostEqual(result["metrics"]["fill_rate"], 2 / 3)
        self.assertEqual(result["trace"][0]["ordered_units"], 6)
        self.assertEqual(result["trace"][1]["ordered_units"], 0)

    def test_backorder_serves_previous_demand_and_keeps_immediate_fill_rate(self):
        scenario = demo_forecast([3, 3, 3], stock=0, forecasts=[[3, 3]] * 3, mode="backorder")
        result = simulate_inventory(**scenario)
        expected = {"total_demand": 9, "fulfilled_units": 9, "backorder_fulfilled_units": 3,
                    "immediately_fulfilled_units": 6, "immediate_unmet_units": 3,
                    "unmet_units": 0, "lost_units": 0, "ending_backorder": 0,
                    "average_backorder": 1, "max_backorder": 3, "total_ordered_units": 12}
        for field, value in expected.items():
            self.assertEqual(result["metrics"][field], value, field)
        self.assertAlmostEqual(result["metrics"]["fill_rate"], 2 / 3)
        self.assertEqual(result["trace"][1]["backorder_fulfilled_units"], 3)

    def test_backorder_remaining_at_horizon_is_unmet_once(self):
        result = simulate_inventory(**demo_fixed([2, 2], stock=0, target=0, lead=5, mode="backorder"))
        for field in ("unmet_units", "ending_backorder", "immediate_unmet_units"):
            self.assertEqual(result["metrics"][field], 4)
        self.assertEqual(result["metrics"]["fulfilled_units"], 0)
        self.assertEqual(result["metrics"]["lost_units"], 0)

    def test_immediate_receipt_serves_remaining_backorder_before_new_demand(self):
        scenario = demo_forecast([2, 1], stock=0, forecasts=[[0], [2]], lead=0, mode="backorder")
        result = simulate_inventory(**scenario)
        second = result["trace"][1]
        self.assertEqual(second["opening_backorder"], 2)
        self.assertEqual(second["inventory_position_before_order"], -2)
        self.assertEqual(second["ordered_units"], 4)
        self.assertEqual(second["immediate_receipts"], 4)
        self.assertEqual(second["backorder_fulfilled_units"], 2)
        self.assertEqual(second["immediate_fulfilled_units"], 1)
        self.assertEqual(second["ending_stock"], 1)
        self.assertEqual(result["metrics"]["unmet_units"], 0)
        self.assertEqual(result["metrics"]["fulfilled_units"], 3)
        self.assertAlmostEqual(result["metrics"]["fill_rate"], 1 / 3)

    def test_lead_time_two_receives_only_at_period_two(self):
        result = simulate_inventory(**demo_fixed([1, 1, 1, 1], stock=0, target=4, lead=2))
        self.assertEqual([row["scheduled_receipts"] for row in result["trace"]], [0, 0, 4, 0])
        self.assertEqual(result["metrics"]["fulfilled_units"], 2)
        self.assertEqual(result["metrics"]["unmet_units"], 2)
        self.assertEqual(result["metrics"]["ending_stock"], 2)
        self.assertEqual(result["metrics"]["total_ordered_units"], 5)
        self.assertEqual(result["ending_orders"], [{"arrival_period": 5, "units": 1}])

    def test_lead_time_zero_receives_before_current_demand(self):
        result = simulate_inventory(**demo_fixed([2, 2], stock=0, target=2, lead=0))
        self.assertEqual(result["metrics"]["fill_rate"], 1)
        self.assertEqual(result["metrics"]["received_units"], 4)
        self.assertTrue(all(row["immediate_receipts"] == 2 for row in result["trace"]))
        self.assertEqual(result["ending_orders"], [])

    def test_review_cadence_and_csl_exclude_partial_final_cycle(self):
        result = simulate_inventory(**demo_fixed([1, 1, 1, 1, 1], stock=10, target=10, review=2))
        self.assertEqual([row["review"] for row in result["trace"]], [True, False, True, False, True])
        self.assertEqual(result["metrics"]["complete_review_cycles"], 2)
        self.assertEqual(result["metrics"]["successful_review_cycles"], 2)
        self.assertEqual(result["metrics"]["excluded_partial_cycle_periods"], 1)
        self.assertEqual(result["metrics"]["cycle_service_level"], 1)

    def test_no_complete_cycle_returns_structured_unavailable(self):
        result = simulate_inventory(**demo_fixed([0], stock=0, target=0, review=2))
        self.assertIsNone(result["metrics"]["cycle_service_level"])
        reason = result["metric_availability"]["cycle_service_level"]["reason"]
        self.assertEqual(reason["code"], "no_complete_review_cycle")

    def test_order_at_t_is_unaffected_by_realized_demand_at_t_or_later(self):
        baseline = demo_forecast([1, 1, 1, 1], stock=0, forecasts=[[1, 1]] * 4)
        changed = copy.deepcopy(baseline)
        changed["demand_series"][2:] = [200, 200]
        original = simulate_inventory(**baseline)
        perturbed = simulate_inventory(**changed)
        self.assertEqual(original["trace"][:2], perturbed["trace"][:2])
        for key in ("ordered_units", "order_target", "forecast_used", "inventory_position_before_order"):
            self.assertEqual(original["trace"][2][key], perturbed["trace"][2][key], key)

    def test_future_forecast_rows_do_not_enter_earlier_orders(self):
        baseline = demo_forecast([1, 1, 1, 1], stock=0, forecasts=[[1, 1]] * 4)
        changed = copy.deepcopy(baseline)
        changed["forecast_series"][2:] = [[900, 900], [800, 800]]
        self.assertEqual(simulate_inventory(**baseline)["trace"][:2], simulate_inventory(**changed)["trace"][:2])

    def test_forecast_sequence_changes_operational_result_without_future_demand_input(self):
        high = demo_forecast([2], stock=0, forecasts=[[2]], lead=0)
        low = demo_forecast([2], stock=0, forecasts=[[0]], lead=0)
        self.assertEqual(simulate_inventory(**high)["metrics"]["fulfilled_units"], 2)
        self.assertEqual(simulate_inventory(**low)["metrics"]["fulfilled_units"], 0)

    def test_stock_demand_and_pipeline_conservation(self):
        for mode in ("lost_sales", "backorder"):
            for lead in (0, 1, 3):
                with self.subTest(mode=mode, lead=lead):
                    result = simulate_inventory(**demo_forecast(
                        [3, 0, 1, 5, 2], stock=2, forecasts=[[2] * (lead + 2)] * 5,
                        lead=lead, review=2, buffer=1, mode=mode,
                    ))
                    metrics = result["metrics"]
                    self.assertAlmostEqual(2 + metrics["received_units"] - metrics["fulfilled_units"], metrics["ending_stock"])
                    self.assertAlmostEqual(metrics["total_demand"], metrics["fulfilled_units"] + metrics["lost_units"] + metrics["ending_backorder"])
                    self.assertTrue(all(abs(error) < 1e-10 for error in result["accounting"].values()))
                    for row in result["trace"]:
                        balance = row["opening_stock"] + row["scheduled_receipts"] + row["immediate_receipts"] - row["fulfilled_units"]
                        self.assertAlmostEqual(balance, row["ending_stock"])

    def test_simulation_is_reproducible_json_finite_and_does_not_mutate_input(self):
        scenario = demo_forecast([0, 1, 0, 3], stock=1, forecasts=[[1, 2]] * 4, mode="backorder")
        before = copy.deepcopy(scenario)
        first = simulate_inventory(**scenario)
        self.assertEqual(simulate_inventory(**scenario), first)
        self.assertEqual(scenario, before)
        self.assertEqual(json.loads(json.dumps(first, allow_nan=False)), first)

    def test_invalid_or_missing_scenario_configuration_is_rejected(self):
        cases = [
            ("initial_stock", -1), ("initial_stock", float("nan")),
            ("initial_stock", float("inf")), ("initial_stock", True),
            ("lead_time_periods", -1), ("lead_time_periods", 1.5),
            ("review_period", 0), ("review_period", True),
            ("stockout_mode", "unknown"), ("policy", None),
            ("policy_parameters", {}), ("policy_parameters", {"target_stock": -1}),
            ("policy_parameters", {"target_stock": 0, "hidden_buffer": 2}),
            ("demand_series", [float("nan")]), ("demand_series", [float("inf")]),
            ("demand_series", [-1]), ("demand_series", []),
            ("forecast_series", [[float("nan")]]), ("forecast_series", [[float("inf")]]),
            ("forecast_series", [[-1]]), ("forecast_series", []),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                scenario = demo_fixed([1], stock=0, target=0)
                scenario[key] = value
                with self.assertRaises(ValueError):
                    simulate_inventory(**scenario)

    def test_missing_required_parameters_has_no_enterprise_defaults(self):
        with self.assertRaises(TypeError):
            simulate_inventory(demand_series=[0], forecast_series=[[]])

    def test_flat_or_short_forecast_input_cannot_borrow_future_rows(self):
        scenario = demo_forecast([1, 1], stock=0, forecasts=[[1], [1]])
        with self.assertRaisesRegex(ValueError, "lead_time_periods"):
            simulate_inventory(**scenario)
        scenario["forecast_series"] = [1, 1]
        with self.assertRaisesRegex(ValueError, "explicit sequence"):
            simulate_inventory(**scenario)

    def test_finite_inputs_that_overflow_are_rejected_without_nonfinite_json(self):
        scenario = demo_forecast([0], stock=0, forecasts=[[1e308, 1e308]])
        with self.assertRaisesRegex(ValueError, "finite"):
            simulate_inventory(**scenario)

    def test_unrepresentable_integer_is_rejected_as_value_error(self):
        scenario = demo_fixed([0], stock=10**1000, target=0)
        with self.assertRaisesRegex(ValueError, "finite"):
            simulate_inventory(**scenario)


if __name__ == "__main__":
    unittest.main(verbosity=2)
