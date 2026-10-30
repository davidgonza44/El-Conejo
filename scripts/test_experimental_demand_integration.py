"""Actual detail API responses on disposable SQLite with synthetic demand only."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_predictive_module import seed, setup_app, login, ADMIN_PASSWORD, WRITE_TABLES
from test_experimental_demand import series
from app.models import Product
from app.services import forecast_service, predictive_cache_service as cache
from app.services import experimental_demand_service as experimental


class ExperimentalIntegrationTests(unittest.TestCase):
    def test_http_contract_states_permissions_and_no_writes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
            app, users = setup_app(Path(root))
            seed(app, users["admin"])
            client = app.test_client()
            with app.app_context():
                product_id = Product.query.filter_by(code="CEM-050").one().id
                counts = [model.query.count() for model in WRITE_TABLES]
            endpoint = f"/api/predictions/products/{product_id}/detail"
            self.assertIn(client.get(endpoint).status_code, (302, 401))
            login(client, "core_admin", ADMIN_PASSWORD)
            for state, horizon, values in (("available", 7, ([1] * 7 + [0] * 7) * 10),
                                           ("limited", 30, ([0] * 6 + [2]) * 30),
                                           ("abstain", 7, ([0] * 19 + [1]) * 20)):
                demand = series(values)
                demand.product_id = product_id
                with (patch("app.services.predictive_data_service.load_series", return_value={product_id: demand}),
                      patch("app.services.predictive_job_service.request_predictions", return_value=("ready", {})),
                      patch("app.services.predictive_analysis_service.prediction_block", return_value=(None, None)),
                      patch("app.services.purchase_service.create_order") as order):
                    response = client.get(endpoint)
                    self.assertEqual(response.status_code, 200)
                    dto = response.json
                    period = next(p for p in dto["periods"] if p["days"] == horizon)
                    self.assertEqual(period["prediction_policy"]["status"], state)
                    self.assertEqual(set(dto["demand_profile"]), {"positive_count", "zero_rate", "adi", "cv2",
                                     "zero_rate_segment", "adi_cv2_classification", "observation_unit"})
                    self.assertEqual(set(period["uncertainty"]), {"available", "method", "nominal_coverage",
                                     "calibration_count", "lower_bound", "upper_bound", "unavailable_reason"})
                    self.assertEqual(set(period["two_part"]), {"horizon", "occurrence_probability",
                                     "positive_demand_expectation", "unconditional_expected_quantity",
                                     "available", "unavailable_reason"})
                    order.assert_not_called()
                    print("SYNTHETIC_HTTP_DTO", json.dumps({"demand_profile": dto["demand_profile"],
                          **{key: period[key] for key in ("two_part", "uncertainty", "prediction_policy")}},
                          ensure_ascii=False, allow_nan=False))
            with app.app_context():
                self.assertEqual(counts, [model.query.count() for model in WRITE_TABLES])

    def test_frozen_registry_and_experimental_cache_fingerprint(self):
        self.assertEqual(tuple(forecast_service.MODELS),
                         ("naive", "mean", "moving_average", "linear_trend", "arima", "random_forest"))
        digest = cache.hashlib.sha256(Path(experimental.__file__).read_bytes()).hexdigest()
        self.assertIn(digest, cache._implementation()[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
