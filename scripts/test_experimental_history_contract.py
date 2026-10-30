"""Synthetic normalized history contracts, loaded without Flask/configuration/database."""
import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest

from experimental_prediction_tools import audit_inputs, load_service

contract = load_service("experimental_history_contract")


def row(day="2024-01-01", quantity=1, observed="observed", operation="open", kind="synthetic"):
    return dict(date=day, product_code="0001", warehouse_code="DEMO-W", unit="unit",
                quantity=quantity, observation_status=observed, operational_status=operation,
                source_id="DEMO-fixture", data_kind=kind, observation_basis="fixture explicit coverage")


def validate(rows):
    return contract.validate_partition(rows, role="development_history", allow_synthetic=True)


class HistoryContractTests(unittest.TestCase):
    def test_roles_remain_separate_and_2025_is_null(self):
        result = contract.prepare_inputs([row()], [row("2026-01-01")], allow_synthetic=True)
        self.assertEqual(result["development_history"]["years"], [2023, 2024])
        self.assertEqual(result["external_evaluation"]["years"], [2026])
        self.assertIsNone(result["unobserved_periods"][0]["quantity"])
        self.assertFalse(result["final_comparison_executed"])
        json.dumps(result, allow_nan=False)

    def test_wrong_year_never_becomes_training_or_zero(self):
        for day in ("2025-01-01", "2026-01-01", "2022-12-31"):
            with self.assertRaises(ValueError):
                validate([row(day)])
        with self.assertRaises(ValueError):
            contract.validate_partition([row()], role="external_evaluation", allow_synthetic=True)

    def test_missing_day_and_unobserved_break_segments_without_fill(self):
        result = validate([row(), row("2024-01-03"),
                           row("2024-01-04", None, "unobserved", "unknown"), row("2024-01-05")])
        segments = result["series"][0]["segments"]
        self.assertEqual([len(segment) for segment in segments], [1, 1, 1])
        self.assertEqual(sum(len(segment) for segment in segments), 3)

    def test_confirmed_closure_bridges_only_operational_observations(self):
        result = validate([row(), row("2024-01-02", "", "unobserved", "closed"), row("2024-01-03")])
        self.assertEqual([len(segment) for segment in result["series"][0]["segments"]], [2])
        self.assertEqual([item["quantity"] for item in result["series"][0]["segments"][0]], [1, 1])

    def test_no_zero_for_closed_unknown_or_unobserved(self):
        for operation in ("closed", "unknown", "open"):
            with self.assertRaises(ValueError):
                validate([row(quantity=0, observed="unobserved", operation=operation)])
        for operation in ("closed", "unknown"):
            with self.assertRaises(ValueError):
                validate([row(operation=operation)])
        self.assertEqual(validate([row(quantity=0)])["rows"][0]["quantity"], 0)

    def test_invalid_columns_quantities_dates_and_ids(self):
        for field, value in (("quantity", float("nan")), ("quantity", float("inf")),
                             ("quantity", -1), ("quantity", True), ("quantity", None), ("quantity", 10**1000),
                             ("date", "01/01/2024"), ("date", "2024-1-1"),
                             ("product_code", 1), ("warehouse_code", ""), ("unit", ""),
                             ("observation_basis", ""), ("source_id", "")):
            invalid = row()
            invalid[field] = value
            with self.subTest(field=field, value=value), self.assertRaises((ValueError, TypeError)):
                validate([invalid])
        invalid = row()
        del invalid["quantity"]
        with self.assertRaises(ValueError):
            validate([invalid])

    def test_duplicates_units_and_calendar_conflicts_rejected(self):
        for other in (row(), {**row("2024-01-02"), "unit": "box"},
                      {**row(quantity=None, observed="unobserved", operation="closed"), "product_code": "0002"}):
            with self.assertRaises(ValueError):
                validate([row(), other])

    def test_synthetic_requires_explicit_fixture_opt_in(self):
        with self.assertRaises(ValueError):
            contract.prepare_inputs([row()], [])
        result = validate([row()])
        self.assertTrue(result["contains_synthetic"])
        self.assertFalse(result["scientific_evidence"])

    def test_code_leading_zero_warehouse_separation_and_input_immutability(self):
        rows = [row(), {**row(), "warehouse_code": "DEMO-2"}]
        before = copy.deepcopy(rows)
        result = validate(rows)
        self.assertEqual(len(result["series"]), 2)
        self.assertTrue(all(item["product_code"] == "0001" for item in result["series"]))
        self.assertEqual(rows, before)

    def test_external_values_cannot_change_development(self):
        one = contract.prepare_inputs([row()], [row("2026-01-01", 1)], allow_synthetic=True)
        two = contract.prepare_inputs([row()], [row("2026-01-01", 9999)], allow_synthetic=True)
        self.assertEqual(one["development_history"], two["development_history"])

    def test_csv_audit_only_uses_explicit_temporary_synthetic_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = [Path(temp) / "development.csv", Path(temp) / "external.csv"]
            for path, day in zip(paths, ("2024-01-01", "2026-01-01")):
                with path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=contract.REQUIRED_COLUMNS)
                    writer.writeheader()
                    # Declared-real label tests the CLI contract; values remain hand-written fixtures.
                    writer.writerow(row(day, kind="real"))
            result = audit_inputs(*paths)
            self.assertFalse(result["final_comparison_executed"])
            self.assertEqual(result["partitions"]["development_history"]["rows"], 1)
            self.assertEqual(list(Path(temp).iterdir()), paths)


if __name__ == "__main__":
    unittest.main(verbosity=2)
