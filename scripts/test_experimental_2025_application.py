"""Opt-in Flask viewer tests using synthetic JSON and fake users; no DB or app factory."""
from __future__ import annotations

import ast
import copy
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from flask import Flask
from flask_login import LoginManager, UserMixin


ROOT = Path(__file__).resolve().parents[1]


def _offline_guard(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "sqlite3.connect"}:
        raise RuntimeError("Database/network prohibited in experimental application tests")
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0]))
        if path.name.lower().startswith(".env") or {"instance", "uploads", "private_imports"}.intersection(part.lower() for part in path.parts):
            raise RuntimeError("Private/configuration access prohibited")


def _load_modules():
    """Load real authorization decorators without evaluating app/configuration/models."""
    packages = {}
    for name, directory in {"app": "app", "app.services": "app/services",
                            "app.routes": "app/routes", "app.controllers": "app/controllers",
                            "app.utils": "app/utils", "app.models": "app/models"}.items():
        module = types.ModuleType(name)
        module.__path__ = [str(ROOT / directory)]
        packages[name] = module
    user_constants = types.ModuleType("app.models.user")
    for name, value in {"ROLE_ADMIN": "admin", "ROLE_INVENTARIO": "inventario",
                        "ROLE_VENDEDOR": "vendedor", "ROLE_SOPORTE_TI": "soporte_ti"}.items():
        setattr(user_constants, name, value)
    packages["app.models.user"] = user_constants
    with patch.dict(sys.modules, packages):
        service = importlib.import_module("app.services.experimental_2025_application_service")
        routes = importlib.import_module("app.routes.experimental_2025")
        auth = importlib.import_module("app.utils.auth_decorators")
    return service, routes, auth


sys.addaudithook(_offline_guard)
SERVICE, ROUTES, AUTH = _load_modules()


def report_fixture():
    metrics = {"wape": 100.0, "mae": 2.0, "rmse": 3.0, "bias": -2.0,
               "mase": 1.5, "rmsse": 1.75, "pair_count": 12}
    horizon = {"time_unit": "calendar_day",
               "coverage": {"tsb": {"sku": {"numerator": 2, "denominator": 3, "percent": 200 / 3},
                                    "volume": {"numerator": 9, "denominator": 10, "percent": 90},
                                    "unavailable_reasons": {"insufficient_positive_training_periods": 1}}},
               "metrics": {"zero": metrics, "tsb": dict(metrics, wape=92)},
               "calibration": {"two_part": {"occurrence": {"brier_score": .2, "count": 12},
                                             "intervals": {"nominal_coverage": .8, "observed_coverage": .75,
                                                           "calibration_count": 12, "evaluation_count": 12,
                                                           "unavailable_reason": {"insufficient_history": 1}}}},
               "abstention": {"tsb": {"abstain": {"sku": {"numerator": 1, "denominator": 3, "percent": 100 / 3},
                                                       "volume": {"numerator": 1, "denominator": 10, "percent": 10},
                                                       "reasons": {"insufficient_positive_events": 1}}}},
               "segments": {"tsb": {"zero_rate_segment": {"intermittent": {"available": False,
                                                      "unavailable_reason": "insufficient_segment_skus", "summary": metrics}},
                                      "adi_cv2_classification": {"smooth": {"available": False, "summary": metrics}}}},
               "inventory": {"scenario": {"lead_time_periods": 2, "review_period": 5,
                                             "parameters_are_enterprise_estimates": False},
                             "results": {"zero_initial_stock:mean": {"sku_count": 2,
                                                         "macro_metrics": {"fill_rate": .9, "order_count": 2},
                                                         "quantity_index_fill_rate": .95}},
                             "unavailable_reasons": {"adida:insufficient_history": 1}},
               "bootstrap": {"tsb": {"available": True, "seed": 2025, "iterations": 100, "N": 12,
                             "N_sku": 6, "unit": "sku_cluster_all_paired_origins",
                             "mean_difference": -.1, "median_difference": -.05,
                             "confidence_interval": [-.2, .03]}}}
    return {"metadata": {"data_kind": "synthetic_structured", "year": 2025,
                         "purpose": "experimental_evaluation"},
            "characterization": {"2025_ONLY": {"universe": 3, "evaluable": 2,
                                                 "segments": {"regular": 1, "intermittent": 2},
                                                 "adi_cv2_categories": {"smooth": 1, "lumpy": 2},
                                                 "reasons": {"no_source_demand": 1}}},
            "views": {name: {"horizons": {"7": copy.deepcopy(horizon), "30": copy.deepcopy(horizon)}}
                      for name in SERVICE.VIEW_KINDS}}


class _User(UserMixin):
    def __init__(self, role):
        self.id = role
        self.role = role


class Experimental2025ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="experimental-2025-viewer-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.report = self.root / "results.json"
        self.report.write_text(json.dumps(report_fixture(), allow_nan=False), encoding="utf-8")
        self.root_patch = patch.object(SERVICE, "REPORT_ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.permissions = {"admin", "inventario"}
        self.guard_patch = patch.object(AUTH, "role_has_permission",
                                       lambda role, permission: role in self.permissions and permission == "predictions:read")
        self.guard_patch.start()
        self.addCleanup(self.guard_patch.stop)
        self.app = Flask("experimental_viewer_test", template_folder=str(ROOT / "app/templates"))
        self.app.config.update(TESTING=True, SECRET_KEY="isolated-fixture", EXPERIMENTAL_2025_REPORT=str(self.report))
        login = LoginManager(self.app)
        login.user_loader(lambda user_id: _User(user_id))
        ROUTES.register_experimental_2025(self.app)
        self.client = self.app.test_client()

    def sign_in(self, role="admin"):
        with self.client.session_transaction() as session:
            session["_user_id"] = role
            session["_fresh"] = True

    def test_blueprint_registration_is_explicit_and_idempotent(self):
        fresh = Flask("not_opted_in")
        self.assertEqual(fresh.test_client().get("/experimental/2025").status_code, 404)
        ROUTES.register_experimental_2025(self.app)
        self.assertEqual(len([rule for rule in self.app.url_map.iter_rules() if rule.endpoint.startswith("experimental_2025.")]), 2)

    def test_anonymous_access_returns_401_on_both_routes(self):
        for path in ("/experimental/2025", "/api/experimental/2025/readiness"):
            self.assertEqual(self.client.get(path).status_code, 401)

    def test_unauthorized_user_returns_403(self):
        self.sign_in("vendedor")
        for path in ("/experimental/2025", "/api/experimental/2025/readiness"):
            self.assertEqual(self.client.get(path).status_code, 403)

    def test_granular_permission_is_authority_instead_of_hardcoded_role(self):
        self.permissions.add("vendedor")
        self.sign_in("vendedor")
        self.assertEqual(self.client.get("/api/experimental/2025/readiness").status_code, 200)

    def test_admin_and_inventory_can_read(self):
        for role in ("admin", "inventario"):
            self.sign_in(role)
            response = self.client.get("/api/experimental/2025/readiness")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json["available"])

    def test_missing_configuration_is_truthful(self):
        self.sign_in()
        self.app.config.pop("EXPERIMENTAL_2025_REPORT")
        response = self.client.get("/api/experimental/2025/readiness")
        self.assertEqual(response.json["reason"], "report_not_configured")
        self.assertFalse(response.json["available"])

    def test_missing_report_is_truthful_and_hides_path(self):
        self.sign_in()
        self.app.config["EXPERIMENTAL_2025_REPORT"] = str(self.root / "missing.json")
        response = self.client.get("/api/experimental/2025/readiness")
        self.assertEqual(response.json["reason"], "report_not_found")
        self.assertNotIn(str(self.root), response.text)

    def test_invalid_json_is_unavailable_without_exception_detail(self):
        self.report.write_text("{invalid", encoding="utf-8")
        self.assertEqual(SERVICE.read_report(self.report)["reason"], "invalid_report")

    def test_nonfinite_json_and_numeric_overflow_are_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity", "1e999"):
            self.report.write_text('{"metadata":{"data_kind":"synthetic_structured","year":2025,"purpose":"experimental_evaluation"},"n":' + value + "}", encoding="utf-8")
            result = SERVICE.read_report(self.report)
            self.assertEqual(result["reason"], "invalid_report")
            json.dumps(result, allow_nan=False)

    def test_extreme_integer_is_rejected_without_numeric_overflow(self):
        fixture = report_fixture()
        fixture["n"] = 10 ** 400
        self.assertEqual(SERVICE.present_report(fixture)["reason"], "invalid_report")

    def test_malformed_optional_aggregates_remain_safe(self):
        for bad in (None, [], "wrong", 1):
            fixture = report_fixture()
            fixture["characterization"] = bad
            fixture["views"]["2025_ONLY"]["horizons"] = bad
            json.dumps(SERVICE.present_report(fixture), allow_nan=False)

    def test_invalid_provenance_is_rejected(self):
        for key, wrong in (("data_kind", "real_observed"), ("year", 2026), ("purpose", "live")):
            fixture = report_fixture()
            fixture["metadata"][key] = wrong
            self.assertEqual(SERVICE.present_report(fixture)["reason"], "invalid_provenance")

    def test_metadata_without_result_views_is_unavailable(self):
        fixture = report_fixture()
        fixture.pop("views")
        self.assertEqual(SERVICE.present_report(fixture)["reason"], "no_evaluable_views")

    def test_failed_source_audit_blocks_experimental_readiness(self):
        fixture = report_fixture()
        fixture["audit"] = {"gate": "FAILED"}
        result = SERVICE.present_report(fixture)
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "audit_failed")

    def test_source_basename_and_temporal_range_survive_without_paths(self):
        fixture = report_fixture()
        fixture["metadata"]["sources"] = [{"filename": "Demanda_limpia_2025_CORREGIDA_FINAL.csv",
                                              "date_range": ["2025-01-01", "2025-12-31"]}]
        result = SERVICE.present_report(fixture)
        source = result["metadata"]["sources"][0]
        self.assertEqual(source["filename"], "Demanda_limpia_2025_CORREGIDA_FINAL.csv")
        self.assertEqual(source["date_range"], ["2025-01-01", "2025-12-31"])

    def test_configuration_and_real_source_ranges_are_available(self):
        fixture = report_fixture()
        fixture["configuration"] = {"aggregation_size": 7, "alpha": .1, "beta": .1,
                                       "models": ["mean", "tsb", "two_part", "adida"], "horizons": [7, 30],
                                       "time_units": {"2025_ONLY": "calendar_day", "2026_views": "operational_day"},
                                       "uncertainty_scope": "two_part_only;other_candidates_not_requested",
                                       "original_protocol_changed": False}
        fixture["audit"] = {"gate": "PASSED_WITH_WARNINGS", "sources": {"demand": {"dates": {
                                     "minimum": "2025-01-01", "maximum": "2025-12-31"}}}}
        fixture["metadata"]["evaluation_2026"] = {"range": ["2026-01-02", "2026-09-12"], "data_kind": "real_observed"}
        result = SERVICE.present_report(fixture)
        self.assertEqual(result["configuration"], fixture["configuration"])
        self.assertEqual(result["source_ranges"]["2025"], {"start": "2025-01-01", "end": "2025-12-31"})
        self.assertEqual(result["source_ranges"]["2026"], {"start": "2026-01-02", "end": "2026-09-12"})

    def test_unknown_observation_segment_is_included_in_chart_denominator(self):
        fixture = report_fixture()
        fixture["characterization"]["2025_ONLY"].update(universe=4,
              segments={"regular": 1, "intermittent": 1, "extremely_intermittent": 1, "unavailable": 1})
        chart = SERVICE.present_report(fixture)["segment_chart"]
        self.assertEqual(sum(row["count"] for row in chart), 4)
        self.assertEqual(sum(row["percent"] for row in chart), 100)
        self.assertEqual(next(row["percent"] for row in chart if row["segment"] == "unavailable"), 25)

    def test_inventory_temporal_comparison_keeps_explicit_common_support(self):
        fixture = report_fixture()
        fixture["temporal_comparison"] = {"7": {"inventory": {"zero_initial_stock:mean": {
            "available": True, "paired_sku_count": 3, "candidate_sku_count": 4, "reference_sku_count": 5,
            "discarded_candidate_skus": 1, "discarded_reference_skus": 2,
            "candidate_macro_metrics": {"fill_rate": .9}, "reference_macro_metrics": {"fill_rate": .85},
            "difference_candidate_minus_reference": {"fill_rate": .05}, "scenario_selected": False,
            "metric_pair_counts": {"fill_rate": {"available_pairs": 3, "unavailable_pairs": 0}}}}}}
        result = SERVICE.present_report(fixture)
        self.assertEqual(result["temporal_comparison"], fixture["temporal_comparison"])

    def test_outside_path_and_csv_are_rejected(self):
        self.assertEqual(SERVICE.read_report(self.root.parent / "outside.json")["reason"], "report_path_not_allowed")
        self.assertEqual(SERVICE.read_report(self.root / "source.csv")["reason"], "report_path_not_allowed")

    def test_identity_price_customer_and_paths_are_excluded(self):
        fixture = report_fixture()
        fixture.update({"product_code": "000042", "product_name": "PRIVATE PRODUCT", "customer": "PRIVATE CUSTOMER",
                        "path": "C:/PRIVATE/source.csv", "unit_price": 9999, "raw_rows": [{"secret": "PRIVATE"}],
                        "rows": [999987654.123], "observations": [999987654.123]})
        fixture["views"]["2025_ONLY"]["horizons"]["7"]["metrics"]["tsb"]["product_name"] = "PRIVATE PRODUCT"
        fixture["views"]["2025_ONLY"]["reason"] = "C:/PRIVATE/source.csv"
        result = json.dumps(SERVICE.present_report(fixture))
        for forbidden in ("000042", "PRIVATE", "product_code", "unit_price", "raw_rows", "999987654"):
            self.assertNotIn(forbidden, result)

    def test_source_metadata_and_separate_provenance_are_retained(self):
        result = SERVICE.read_report(self.report)
        self.assertEqual(result["metadata"]["data_kind"], "synthetic_structured")
        self.assertEqual(result["views"]["2026_REAL_ONLY"]["provenance"]["evaluation"], "real_observed")
        self.assertEqual(result["views"]["2025_TO_2026_TRANSFER"]["provenance"]["training"], "synthetic_structured")
        self.assertEqual(result["views"]["2025_PLUS_2026_OBSERVED_TO_DATE"]["provenance"]["training"], ["synthetic_structured", "real_observed"])

    def test_all_metrics_coverage_simulation_and_calibration_are_retained(self):
        result = SERVICE.read_report(self.report)
        self.assertEqual(len(result["metric_rows"]), 16)
        self.assertEqual(result["metric_rows"][0]["wape"], 100)
        self.assertEqual(result["metric_rows"][0]["mase"], 1.5)
        horizon = result["views"]["2025_ONLY"]["horizons"]["7"]
        self.assertEqual(horizon["coverage"]["tsb"]["volume"]["percent"], 90)
        self.assertEqual(horizon["inventory"]["results"]["zero_initial_stock:mean"]["macro_metrics"]["order_count"], 2)
        self.assertEqual(horizon["calibration"]["two_part"]["intervals"]["observed_coverage"], .75)

    def test_actual_schema_reasons_categories_bootstrap_and_inventory_retained(self):
        result = SERVICE.read_report(self.report)
        horizon = result["views"]["2025_ONLY"]["horizons"]["7"]
        self.assertEqual(horizon["coverage"]["tsb"]["unavailable_reasons"], {"insufficient_positive_training_periods": 1})
        self.assertEqual(horizon["calibration"]["two_part"]["intervals"]["unavailable_reason"], {"insufficient_history": 1})
        self.assertEqual(horizon["inventory"]["unavailable_reasons"], {"adida:insufficient_history": 1})
        self.assertEqual(horizon["bootstrap"]["tsb"]["N_sku"], 6)
        self.assertEqual(horizon["bootstrap"]["tsb"]["unit"], "sku_cluster_all_paired_origins")
        self.assertEqual(result["characterization"]["2025_ONLY"]["adi_cv2_categories"]["lumpy"], 2)

    def test_unknown_scenario_or_reason_dictionary_cannot_expose_identities(self):
        fixture = report_fixture()
        horizon = fixture["views"]["2025_ONLY"]["horizons"]["7"]
        horizon["inventory"]["results"]["private_product:mean"] = {"order_count": 123}
        horizon["inventory"]["unavailable_reasons"]["private_person"] = 1
        encoded = json.dumps(SERVICE.present_report(fixture))
        self.assertNotIn("private_product", encoded)
        self.assertNotIn("private_person", encoded)

    def test_unverified_snapshot_identity_reason_is_retained(self):
        fixture = report_fixture()
        fixture["views"]["2025_ONLY"]["horizons"]["7"]["inventory"]["unavailable_reasons"] = {
            "snapshot_identity_unverified": 1}
        result = SERVICE.present_report(fixture)
        self.assertEqual(result["views"]["2025_ONLY"]["horizons"]["7"]["inventory"]["unavailable_reasons"],
                         {"snapshot_identity_unverified": 1})

    def test_temporal_unit_is_retained_and_rendered_without_calendar_day_claim(self):
        fixture = report_fixture()
        fixture["views"]["2026_REAL_ONLY"]["horizons"]["7"]["time_unit"] = "operational_day"
        self.report.write_text(json.dumps(fixture, allow_nan=False), encoding="utf-8")
        self.sign_in()
        self.assertIn("Días operativos", self.client.get("/experimental/2025").text)

    def test_scaled_metric_dtos_render_values_without_assuming_availability(self):
        fixture = report_fixture()
        fixture["views"]["2025_ONLY"]["horizons"]["7"]["metrics"]["tsb"] = {
            "legacy_metrics": {"wape": 92}, "scaled_metrics": {"mase": {"available": True, "value": 1.5},
                                                                  "rmsse": {"available": False, "value": None}}}
        rows = SERVICE.present_report(fixture)["metric_rows"]
        tsb = next(row for row in rows if row["view"] == "2025_ONLY" and row["horizon"] == "7" and row["model"] == "tsb")
        self.assertEqual(tsb["mase"], 1.5)
        self.assertIsNone(tsb["rmsse"])

    def test_rendered_page_contains_tables_svg_and_provenance(self):
        self.sign_in()
        response = self.client.get("/experimental/2025")
        self.assertEqual(response.status_code, 200)
        for expected in ("synthetic_structured", "real_observed", "<table>", "<svg", "100.0000", "experimental_evaluation"):
            self.assertIn(expected, response.text)

    def test_missing_report_renders_empty_state(self):
        self.sign_in()
        self.app.config.pop("EXPERIMENTAL_2025_REPORT")
        response = self.client.get("/experimental/2025")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Reporte no disponible", response.text)

    def test_get_does_not_mutate_report(self):
        self.sign_in()
        before = self.report.read_bytes()
        self.client.get("/experimental/2025")
        self.client.get("/api/experimental/2025/readiness")
        self.assertEqual(before, self.report.read_bytes())

    def test_write_methods_are_not_registered(self):
        self.sign_in()
        for path in ("/experimental/2025", "/api/experimental/2025/readiness"):
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                self.assertEqual(self.client.open(path, method=method).status_code, 405)

    def test_reproducible_and_finite(self):
        first, second = SERVICE.read_report(self.report), SERVICE.read_report(self.report)
        self.assertEqual(first, second)
        json.dumps(first, allow_nan=False)

    def test_new_code_has_no_database_order_or_live_imports(self):
        paths = ("app/services/experimental_2025_application_service.py",
                 "app/controllers/experimental_2025_controller.py", "app/routes/experimental_2025.py")
        for path in paths:
            tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
            imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            imports.extend(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
            for imported in imports:
                self.assertFalse(any(forbidden in imported for forbidden in ("sqlalchemy", "pymysql", "models", "purchase", "replenishment", "extensions", "config")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
