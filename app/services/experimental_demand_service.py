"""Experimental horizon-level baseline; no stock decisions or fitted-model registry.

Policy constants are conservative engineering choices, not validated business rules.
Zero-rate bins are descriptive, separate from ADI/CV² (Syntetos et al., 2005).
Chronological nonoverlapping blocks avoid shared targets; independence/stationarity
are assumptions, not established facts. Intervals have nominal, not proven coverage.
"""
import math
import sys
from datetime import date, timedelta
from statistics import mean, variance

HORIZONS = (7, 30)
REGULAR_ZERO_LIMIT = 0.5
EXTREME_ZERO_LIMIT = 0.95
ADI_LIMIT = 1.32
CV2_LIMIT = 0.49
MIN_FIT_BLOCKS = 4
MIN_POSITIVE_BLOCKS = 2
MIN_CALIBRATION_BLOCKS = 9
NOMINAL_COVERAGE = 0.8
INTERVAL_METHOD = "chronological_split_absolute_residual_quantile"


def _valid_values(values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool)
               and math.isfinite(v) and 0 <= v <= sys.float_info.max / max(1, len(values))
               for v in values)


def adi_cv2_classification(adi, cv2):
    if adi is None or cv2 is None:
        return "unavailable"
    if adi < ADI_LIMIT:
        return "smooth" if cv2 < CV2_LIMIT else "erratic"
    return "intermittent" if cv2 < CV2_LIMIT else "lumpy"


def demand_profile(series):
    values = series.values if _valid_values(series.values) else []
    positive = [v for v in values if v > 0]
    count = len(positive)
    zero = 1 - count / len(values) if values else None
    adi = len(values) / count if count else None
    # Normalize before squaring: tiny/huge finite quantities must stay JSON-safe.
    positive_mean = mean(positive) if positive else None
    cv2 = variance([v / positive_mean for v in positive]) if count >= 2 else None
    segment = "unavailable"
    if zero is not None:
        segment = ("regular" if zero < REGULAR_ZERO_LIMIT else
                   "intermittent" if zero < EXTREME_ZERO_LIMIT else "extremely_intermittent")
    return {"positive_count": count, "zero_rate": zero, "adi": adi, "cv2": cv2,
            "zero_rate_segment": segment,
            "adi_cv2_classification": adi_cv2_classification(adi, cv2),
            "observation_unit": "calendar_week" if series.granularity == "weekly" else series.time_unit}


def complete_blocks(values, horizon):
    """Discard oldest partial prefix; every block is a full, distinct horizon."""
    offset = len(values) % horizon
    return [(start, start + horizon, sum(values[start:start + horizon]))
            for start in range(offset, len(values), horizon)]


def _unavailable(horizon, reason):
    return {"horizon": horizon, "occurrence_probability": None,
            "positive_demand_expectation": None, "unconditional_expected_quantity": None,
            "available": False, "unavailable_reason": reason}


def _uncertainty(reason, count=0):
    return {"available": False, "method": INTERVAL_METHOD,
            "nominal_coverage": NOMINAL_COVERAGE, "calibration_count": count,
            "lower_bound": None, "upper_bound": None, "unavailable_reason": reason}


def _blocked_reason(series, horizon, eligibility, profile):
    if eligibility.status == "INACTIVE":
        return "inactive_product"
    if not _valid_values(series.values):
        return "invalid_history"
    if (horizon not in HORIZONS or series.granularity != "daily"
            or series.time_unit != "calendar_day"):
        return "unsupported_observation_unit_or_horizon"
    if (len(series.starts) != len(series.values) or not all(type(d) is date for d in series.starts) or
            any(b - a != timedelta(days=1) for a, b in zip(series.starts, series.starts[1:]))):
        return "incomplete_calendar"
    if profile["positive_count"] < 2:
        return "insufficient_positive_events"
    if profile["zero_rate_segment"] == "extremely_intermittent":
        return "extreme_intermittency"
    if eligibility.status not in ("ELIGIBLE", "INTERMITTENT"):
        return "insufficient_history"
    return None


def analyze(series, horizon, eligibility):
    """Explicit eligible/intermittent route; unchanged legacy eligibility gates.

    Fit and calibration use disjoint chronological full horizon blocks. A fixed
    fit estimate is retained during calibration (no refit on calibration targets).
    Neither this estimate nor its bounds feed live replenishment arithmetic.
    """
    profile = demand_profile(series)
    reason = _blocked_reason(series, horizon, eligibility, profile)
    result = {"demand_profile": profile, "two_part": _unavailable(horizon, reason),
              "uncertainty": _uncertainty(reason),
              "prediction_policy": {"status": "abstain", "reason": [reason] if reason else []}}
    if reason:
        return result
    blocks = complete_blocks(series.values, horizon)
    split = max(MIN_FIT_BLOCKS, len(blocks) // 2)
    fit, calibration = blocks[:split], blocks[split:]
    positives = [total for _, _, total in fit if total > 0]
    reason = ("insufficient_history" if len(fit) < MIN_FIT_BLOCKS else
              "insufficient_positive_events" if len(positives) < MIN_POSITIVE_BLOCKS else None)
    if reason:
        result.update(two_part=_unavailable(horizon, reason), uncertainty=_uncertainty(reason),
                      prediction_policy={"status": "abstain", "reason": [reason]})
        return result
    probability, magnitude = len(positives) / len(fit), mean(positives)
    expected = probability * magnitude
    result["two_part"] = {"horizon": horizon, "occurrence_probability": probability,
                          "positive_demand_expectation": magnitude,
                          "unconditional_expected_quantity": expected,
                          "available": True, "unavailable_reason": None}
    count = len(calibration)
    result["uncertainty"] = _uncertainty("insufficient_independent_calibration", count)
    result["prediction_policy"] = {"status": "limited", "reason": ["insufficient_independent_calibration"]}
    if count >= MIN_CALIBRATION_BLOCKS:
        residuals = sorted(abs(total - expected) for _, _, total in calibration)
        rank = math.ceil((count + 1) * NOMINAL_COVERAGE)
        radius = residuals[rank - 1]
        result["uncertainty"].update(available=True, lower_bound=max(0.0, expected - radius),
                                     upper_bound=expected + radius, unavailable_reason=None)
        result["prediction_policy"] = {"status": "available", "reason": []}
    return result
