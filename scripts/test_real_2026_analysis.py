"""Isolated real-2026 analysis contracts; no dotenv, MySQL or live HTTP ports."""
import json
import math
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from approval_test_support import IsolatedCase
from flask_login import login_user, logout_user
from sklearn.ensemble import RandomForestRegressor
from sqlalchemy import select

from app.extensions import db
from app.models import Product, StockMovement
from app.models.real_2026 import Real2026Daily, Real2026Import, Real2026Product
from app.services import forecast_advanced_service as advanced, real_2026_analysis_service as analysis
from app.services import real_2026_source_service as source
from app.services.exceptions import NotFoundError, ValidationError


def operational_calendar():
    days, day = [], date(2026, 1, 1)
    while len(days) < 216:
        if day.weekday() != 6:
            days.append(day)
        day += timedelta(days=1)
    return days


class Real2026AnalysisTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.calendar = operational_calendar()
        self.batch = self.make_batch()
        self.historical = self.make_product(self.batch, self.product.code)
        self.fill(self.historical)
        self.before = self.operational_state()

    def make_batch(self, status="confirmed"):
        batch = Real2026Import(
            package_hash=uuid4().hex * 2, status=status, confirmed_slot=1 if status == "confirmed" else None,
            coverage_start=self.calendar[0], coverage_end=self.calendar[-1],
            calendar=[day.isoformat() for day in self.calendar], summary={"synthetic_fixture": True},
            source_hashes={"synthetic.csv": "a" * 64}, provenance={"kind": "synthetic_test_only"},
            created_by_user_id=self.users["admin"].id, preview_token_hash="b" * 64,
            preview_token_expires_at=datetime(2026, 10, 2),
            confirmed_at=datetime(2026, 10, 1) if status != "preview" else None)
        db.session.add(batch)
        db.session.commit()
        return batch

    def make_product(self, batch, code, *, included=True, service=False, cohort="STRICT"):
        product = Real2026Product(batch_id=batch.id, code=code, name="Mercancía histórica sintética",
                                  unit="UNIDAD_ORIGEN", cohort=cohort, identity_status="confirmed",
                                  unit_status="source_unit", is_service=service, included=included,
                                  reason="Fixture sintética; nunca utiliza datos privados.")
        db.session.add(product)
        db.session.commit()
        return product

    def fill(self, product, *, start=0, amount=Decimal("1.125000"), positives=None):
        for index, day in enumerate(self.calendar[start:]):
            quantity = amount if positives is None or index < positives else Decimal("0")
            db.session.add(Real2026Daily(product_id=product.id, event_date=day, quantity=quantity))
        db.session.commit()

    def context(self, user="admin"):
        context = self.app.test_request_context()
        context.push()
        self.addCleanup(context.pop)
        if user:
            login_user(self.users[user])
        else:
            logout_user()

    def operational_state(self):
        return tuple(tuple(db.session.execute(select(*model.__table__.columns)).all())
                     for model in (Product, StockMovement))

    def tearDown(self):
        self.assertEqual(self.before, self.operational_state(), "El análisis escribió datos operativos")
        super().tearDown()

    def test_exact_history_calendar_cutoff_and_independent_catalog(self):
        self.context()
        result = analysis.product_history(self.batch.public_id, self.historical.id)
        self.assertEqual(result["calendar"], [day.isoformat() for day in self.calendar])
        self.assertEqual(result["summary"], {"count": 216, "positive_count": 216,
                         "zero_count": 0, "zero_ratio": 0.0, "total": "243.000000",
                         "first_date": self.calendar[0].isoformat(), "last_date": self.calendar[-1].isoformat()})
        self.assertEqual(result["points"][0]["quantity"], "1.125000")
        self.assertTrue(result["sufficiency"]["eligible"])
        self.assertNotIn("stock", json.dumps(result).lower())
        catalog = analysis.products(self.batch.public_id, search=self.product.code)
        self.assertEqual(catalog["total"], 1)
        self.assertEqual(catalog["items"][0]["name"], "Mercancía histórica sintética")
        json.dumps(result, allow_nan=False)

    def test_suffix_preserved_and_missing_observation_never_becomes_zero(self):
        suffix = self.make_product(self.batch, "SUFFIX")
        self.fill(suffix, start=10, positives=12)
        self.context()
        result = analysis.product_history(self.batch.public_id, suffix.id)
        self.assertEqual(result["summary"]["count"], 206)
        self.assertEqual(result["summary"]["first_date"], self.calendar[10].isoformat())
        self.assertEqual(result["summary"]["zero_count"], 194)
        self.assertEqual(result["points"][-1]["quantity"], "0.000000")
        Real2026Daily.query.filter_by(product_id=suffix.id, event_date=self.calendar[30]).delete()
        db.session.commit()
        with self.assertRaises(ValidationError) as error:
            analysis.product_history(self.batch.public_id, suffix.id)
        self.assertEqual(error.exception.status_code, 409)

    def future_days(self, horizon):
        days, day = [], self.calendar[-1]
        while len(days) < horizon:
            day += timedelta(days=1)
            if day.weekday() != 6:
                days.append(day)
        return days

    def test_actual_moving_average_forecast_uses_operational_periods_after_cutoff(self):
        self.context()
        for horizon in (7, 30):
            result = analysis.analyze(self.batch.public_id, self.historical.id, horizon=horizon)
            forecast = result["forecast"]
            self.assertEqual(forecast["status"], "available")
            self.assertEqual(forecast["cutoff"], self.calendar[-1].isoformat())
            self.assertEqual(forecast["time_unit"], "operational_day")
            self.assertEqual(forecast["points"], [{"period": n, "date": day.isoformat(), "quantity": 1.125}
                                                  for n, day in enumerate(self.future_days(horizon), 1)])
            self.assertEqual(forecast["total"], 1.125 * horizon)
            self.assertIn(self.batch.public_id, result["namespace"])
            self.assertIn("analysis_generated_at", result)
            self.assertNotIn("recommendation", result)
            json.dumps(result, allow_nan=False)

    def test_predictor_receives_exact_calendar_and_unavailability_has_no_fallback(self):
        self.context()
        with patch.object(analysis.forecast_service, "predict", return_value=[2.5] * 7) as predict:
            result = analysis.analyze(self.batch.public_id, self.historical.id)
            self.assertEqual(result["forecast"]["status"], "available")
            self.assertEqual(predict.call_args.kwargs, {"starts": self.calendar, "operational": True})
        with patch.object(analysis.forecast_service, "predict", side_effect=analysis.forecast_service.ModelUnavailable("private detail")):
            result = analysis.analyze(self.batch.public_id, self.historical.id)
            self.assertEqual(result["forecast"]["status"], "model_unavailable")
            self.assertEqual(result["forecast"]["points"], [])
            self.assertIsNone(result["forecast"]["total"])
            self.assertNotIn("private detail", json.dumps(result))
        with patch.object(analysis.forecast_service, "predict", side_effect=RuntimeError("private execution")):
            result = analysis.analyze(self.batch.public_id, self.historical.id)
            self.assertEqual(result["forecast"]["status"], "error")
            self.assertEqual(result["forecast"]["points"], [])
            self.assertNotIn("private execution", json.dumps(result))

    def varied(self, product, pattern):
        for index, day in enumerate(self.calendar):
            db.session.add(Real2026Daily(product_id=product.id, event_date=day,
                                         quantity=Decimal(pattern(index, day))))
        db.session.commit()

    def test_real_random_forest_forecasts_7_and_30_operational_periods(self):
        varied = self.make_product(self.batch, "RF-VARIED")
        self.varied(varied, lambda i, day: 3 + (i % 5) + (2 if day.weekday() == 5 else 0))
        self.context()
        for horizon in (7, 30):
            with patch.object(advanced, "_fit_random_forest", wraps=advanced._fit_random_forest) as fit:
                result = analysis.analyze(self.batch.public_id, varied.id, model="random_forest", horizon=horizon)
            forecast = result["forecast"]
            self.assertEqual(forecast["status"], "available", forecast["reason"])
            self.assertEqual(fit.call_count, 1)
            expected = self.future_days(horizon)
            dates = [date.fromisoformat(point["date"]) for point in forecast["points"]]
            self.assertEqual(dates, expected)
            self.assertEqual([point["period"] for point in forecast["points"]], list(range(1, horizon + 1)))
            self.assertEqual(len(set(dates)), horizon)
            self.assertTrue(all(a < b for a, b in zip(dates, dates[1:])))
            self.assertGreater(dates[0], self.calendar[-1])
            self.assertNotIn(6, {day.weekday() for day in dates})
            self.assertTrue(all(math.isfinite(p["quantity"]) and p["quantity"] >= 0 for p in forecast["points"]))
            self.assertAlmostEqual(forecast["total"], sum(p["quantity"] for p in forecast["points"]))
            self.assertEqual(forecast["future_calendar"]["calendar_days"], (expected[-1] - self.calendar[-1]).days)
            self.assertGreater(forecast["future_calendar"]["calendar_days"], horizon if horizon > 6 else 0)
            self.assertEqual(forecast["method_label"], "Random Forest")
            json.dumps(result, allow_nan=False)

    def test_random_forest_uses_real_regressor_with_exact_future_calendar(self):
        varied = self.make_product(self.batch, "RF-REAL")
        self.varied(varied, lambda i, day: 1 + (i % 7))
        self.context()
        estimators = []

        def fit(*args):
            estimator = original(*args)
            estimators.append(estimator)
            return estimator

        original = advanced._fit_random_forest
        with patch.object(advanced, "_fit_random_forest", side_effect=fit), \
                patch.object(analysis.forecast_service, "predict", wraps=analysis.forecast_service.predict) as predict:
            result = analysis.analyze(self.batch.public_id, varied.id, model="random_forest", horizon=30)
        self.assertEqual(result["forecast"]["status"], "available")
        self.assertEqual(len(estimators), 1)
        self.assertIsInstance(estimators[0], RandomForestRegressor)
        self.assertEqual((estimators[0].n_estimators, estimators[0].max_depth, estimators[0].random_state), (50, 6, 42))
        self.assertTrue(hasattr(estimators[0], "estimators_"))
        self.assertEqual(predict.call_args.kwargs, {"starts": self.calendar, "operational": True,
                                                    "future_starts": self.future_days(30)})

    def test_random_forest_features_never_use_information_after_origin(self):
        varied = self.make_product(self.batch, "RF-CAUSAL")
        self.varied(varied, lambda i, day: (i * 7) % 11 + 1)
        values = [float((i * 7) % 11 + 1) for i in range(len(self.calendar))]
        rows, targets = advanced.supervised_rows(values, self.calendar, "daily")
        for offset, (row, target) in enumerate(zip(rows, targets)):
            index = offset + 28
            self.assertEqual(target, values[index])
            self.assertEqual(row[:4], [values[index - lag] for lag in (1, 7, 14, 28)])
            self.assertEqual(row[4], sum(values[index - 7:index]) / 7)
            self.assertEqual(row[5:], [self.calendar[index].weekday(), self.calendar[index].month])
        calls = []
        original = advanced.rf_features

        def spy(history, target_date, granularity):
            calls.append((list(history), target_date))
            return original(history, target_date, granularity)

        self.context()
        with patch.object(advanced, "rf_features", side_effect=spy):
            result = analysis.analyze(self.batch.public_id, varied.id, model="random_forest", horizon=7)
        path = [point["quantity"] for point in result["forecast"]["points"]]
        recursive = [(history, day) for history, day in calls if day > self.calendar[-1]]
        self.assertEqual([day for _, day in recursive], self.future_days(7))
        for step, (history, _) in enumerate(recursive):
            self.assertEqual(history, values + path[:step])

    def test_random_forest_insufficient_and_intermittent_series(self):
        short = self.make_product(self.batch, "RF-SHORT")
        self.fill(short, start=len(self.calendar) - 59)
        sparse = self.make_product(self.batch, "RF-SPARSE")
        self.varied(sparse, lambda i, day: 4 if i % 9 == 0 else 0)
        self.context()
        with patch.object(advanced, "_fit_random_forest") as fit:
            result = analysis.analyze(self.batch.public_id, short.id, model="random_forest", horizon=30)
        fit.assert_not_called()
        self.assertEqual(result["forecast"]["status"], "ineligible")
        self.assertEqual(result["forecast"]["reason_code"], "insufficient_train_observations")
        self.assertEqual(result["forecast"]["points"], [])
        result = analysis.analyze(self.batch.public_id, sparse.id, model="random_forest", horizon=30)
        self.assertGreater(result["summary"]["zero_ratio"], 0.8)
        forecast = result["forecast"]
        self.assertEqual(forecast["status"], "available", forecast["reason"])
        self.assertEqual(len(forecast["points"]), 30)
        self.assertTrue(all(math.isfinite(p["quantity"]) and p["quantity"] >= 0 for p in forecast["points"]))

    def test_random_forest_without_future_calendar_is_rejected_without_fallback(self):
        self.context()
        with patch.object(analysis, "_future_starts", return_value=None), \
                patch.object(analysis.forecast_service, "predict") as predict:
            result = analysis.analyze(self.batch.public_id, self.historical.id, model="random_forest")
            predict.assert_not_called()
            self.assertEqual(result["forecast"]["status"], "model_unavailable")
            self.assertEqual(result["forecast"]["reason_code"], "future_calendar_required")
            self.assertEqual(result["forecast"]["points"], [])
            self.assertIsNone(result["forecast"]["total"])
            self.assertEqual(result["forecast"]["model"], "random_forest")
        with self.assertRaises(advanced.ModelUnavailable):
            analysis.forecast_service.predict([1.0] * 216, 7, "random_forest", "daily", starts=self.calendar, operational=True)

    def test_future_calendar_rule_reuses_documented_closures_and_openings(self):
        self.assertEqual(source.operational_days_after(date(2026, 9, 18), 2), [date(2026, 9, 19), date(2026, 9, 21)])
        self.assertEqual(source.operational_days_after(date(2026, 4, 1), 1), [date(2026, 4, 6)])
        self.assertEqual(source.operational_days_after(date(2026, 5, 2), 1), [date(2026, 5, 3)])
        self.assertEqual(len(source.operational_days_after(date(2026, 9, 18), 30)), 30)

    def test_invalid_model_paths_never_become_a_zero_forecast(self):
        self.context()
        for path in ([1.0] * 6, [float("nan")] * 7, [-1.0] * 7):
            with patch.object(analysis.forecast_service, "predict", return_value=path):
                result = analysis.analyze(self.batch.public_id, self.historical.id)
                self.assertEqual(result["forecast"]["status"], "model_unavailable")
                self.assertEqual(result["forecast"]["reason_code"], "invalid_prediction_values")
                self.assertEqual(result["forecast"]["points"], [])
                self.assertIsNone(result["forecast"]["total"])
                json.dumps(result, allow_nan=False)

    def test_sufficiency_exclusions_and_sensitivity_are_explicit(self):
        short = self.make_product(self.batch, "SHORT")
        self.fill(short, start=170)
        sparse = self.make_product(self.batch, "SPARSE")
        self.fill(sparse, positives=11)
        excluded = self.make_product(self.batch, "QUARANTINE", included=False, cohort="QUARANTINE")
        service = self.make_product(self.batch, "SERVICE", included=False, service=True)
        sensitivity = self.make_product(self.batch, "SENSITIVITY", cohort="SENSITIVITY")
        self.fill(sensitivity)
        self.context()
        for product, reason in ((short, "insufficient_train_observations"),
                                (sparse, "insufficient_train_positive_observations"),
                                (excluded, "product_excluded"), (service, "service_excluded")):
            with patch.object(analysis.forecast_service, "predict") as predict:
                result = analysis.analyze(self.batch.public_id, product.id)
                self.assertEqual(result["forecast"]["status"], "ineligible")
                self.assertEqual(result["forecast"]["reason_code"], reason)
                self.assertEqual(result["forecast"]["points"], [])
                predict.assert_not_called()
        result = analysis.analyze(self.batch.public_id, sensitivity.id)
        self.assertTrue(result["forecast"]["sensitivity_only"])
        self.assertIn("Solo sensibilidad", result["unit_notice"])
        self.assertEqual(result["product"]["unit"], "UNIDAD_ORIGEN")

    def test_preview_and_cross_batch_products_are_not_analyzable(self):
        preview = self.make_batch("preview")
        other = self.make_product(preview, self.product.code)
        self.context()
        for operation in (lambda: analysis.products(preview.public_id),
                          lambda: analysis.product_history(preview.public_id, other.id),
                          lambda: analysis.product_history(self.batch.public_id, other.id)):
            with self.assertRaises(NotFoundError):
                operation()

    def test_revision_keeps_old_history_auditable_without_combining_versions(self):
        self.batch.status, self.batch.confirmed_slot = "superseded", None
        db.session.commit()
        revised = self.make_batch()
        replacement = self.make_product(revised, self.product.code)
        self.fill(replacement, amount=Decimal("2.000000"))
        self.context()
        old = analysis.analyze(self.batch.public_id, self.historical.id)
        new = analysis.analyze(revised.public_id, replacement.id)
        self.assertEqual(old["summary"]["total"], "243.000000")
        self.assertTrue(old["batch"]["archived"])
        self.assertEqual(old["forecast"]["reason_code"], "superseded_batch")
        self.assertEqual(new["summary"]["total"], "432.000000")
        self.assertEqual(new["forecast"]["points"][0]["quantity"], 2.0)
        self.assertNotEqual(old["namespace"], new["namespace"])

    def test_service_permissions_and_parameters_fail_before_reading_data(self):
        self.context(user=None)
        with self.assertRaises(ValidationError) as error:
            analysis.products(self.batch.public_id)
        self.assertEqual(error.exception.status_code, 401)
        login_user(self.users["vendedor"])
        with self.assertRaises(ValidationError) as error:
            analysis.products(self.batch.public_id)
        self.assertEqual(error.exception.status_code, 403)
        login_user(self.users["admin"])
        with patch.object(analysis, "role_has_permission", side_effect=lambda _role, permission: permission == "historical_imports:read"):
            self.assertEqual(analysis.products(self.batch.public_id)["total"], 1)
            with self.assertRaises(ValidationError) as error:
                analysis.analyze(self.batch.public_id, self.historical.id)
            self.assertEqual(error.exception.status_code, 403)
        for kwargs in ({"horizon": True}, {"horizon": 90}, {"model": "auto"}):
            with self.assertRaises(ValidationError):
                analysis.analyze(self.batch.public_id, self.historical.id, **kwargs)
        with self.assertRaises(ValidationError):
            analysis.products(self.batch.public_id, page=0)


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
