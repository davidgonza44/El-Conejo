"""Pure normalized daily-input contract; no import, database or evaluation side effects."""
from collections import defaultdict
from datetime import date, timedelta
import math
import re

REQUIRED_COLUMNS = (
    "date", "product_code", "warehouse_code", "unit", "quantity",
    "observation_status", "operational_status", "source_id", "data_kind",
    "observation_basis",
)
ROLE_YEARS = {"development_history": (2023, 2024), "external_evaluation": (2026,)}


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must use YYYY-MM-DD")
    return date.fromisoformat(value)


def _quantity(value, observed):
    if not observed:
        if value not in (None, ""):
            raise ValueError("Unobserved quantities must be null/empty, never zero")
        return None
    if isinstance(value, bool) or value in (None, ""):
        raise ValueError("Observed quantity must be explicitly recorded")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Observed quantity must be a representable number") from error
    if not math.isfinite(result) or result < 0:
        raise ValueError("Observed quantity must be finite and nonnegative")
    return result


def _normalize(row, role, allow_synthetic):
    if any(column not in row for column in REQUIRED_COLUMNS):
        raise ValueError("Missing required input columns")
    day = _day(row["date"])
    if day.year not in ROLE_YEARS[role]:
        raise ValueError("Year outside declared role; 2025 is an unobserved gap")
    for column in ("product_code", "warehouse_code", "unit", "source_id", "observation_basis"):
        if not isinstance(row[column], str) or not row[column].strip():
            raise ValueError("Identity, unit and observation evidence must be explicit strings")
    kind = row["data_kind"]
    if kind not in ("real", "synthetic") or (kind == "synthetic" and not allow_synthetic):
        raise ValueError("Synthetic inputs are fixtures only, not scientific evidence")
    status, operation = row["observation_status"], row["operational_status"]
    if status not in ("observed", "unobserved") or operation not in ("open", "closed", "unknown"):
        raise ValueError("Invalid explicit observation/operation status")
    if status == "observed" and operation != "open":
        raise ValueError("Observed demand requires a confirmed operational day")
    return {**row, "date": day.isoformat(), "quantity": _quantity(row["quantity"], status == "observed")}


def _segments(rows):
    """Closed confirmed days can bridge operational observations; unknown/missing cannot."""
    segments, current, previous = [], [], None
    for row in sorted(rows, key=lambda item: item["date"]):
        day = _day(row["date"])
        gap = previous is not None and day - previous != timedelta(days=1)
        unavailable = row["observation_status"] == "unobserved" and row["operational_status"] != "closed"
        if gap or unavailable:
            if current:
                segments.append(current)
            current = []
        if row["observation_status"] == "observed":
            current.append(row)
        previous = day
    if current:
        segments.append(current)
    return segments


def validate_partition(rows, *, role, allow_synthetic=False):
    """Validate only. Never zero-fill, join disconnected segments, tune or fit."""
    if role not in ROLE_YEARS:
        raise ValueError("Unknown partition role")
    normalized = [_normalize(row, role, allow_synthetic) for row in rows]
    grouped, seen, operations, units = defaultdict(list), set(), {}, {}
    for row in normalized:
        identity = row["product_code"], row["warehouse_code"]
        key = (*identity, row["date"])
        if key in seen:
            raise ValueError("Duplicate daily product/warehouse row")
        seen.add(key)
        calendar_key = row["warehouse_code"], row["date"]
        if operations.setdefault(calendar_key, row["operational_status"]) != row["operational_status"]:
            raise ValueError("Contradictory warehouse operational calendar")
        if units.setdefault(identity, row["unit"]) != row["unit"]:
            raise ValueError("Unit conversions must be audited upstream")
        grouped[identity].append(row)
    series = [{"product_code": product, "warehouse_code": warehouse, "unit": units[(product, warehouse)],
               "segments": _segments(grouped[(product, warehouse)])}
              for product, warehouse in sorted(grouped)]
    return {"role": role, "years": list(ROLE_YEARS[role]), "rows": normalized, "series": series,
            "contains_synthetic": any(row["data_kind"] == "synthetic" for row in normalized),
            "scientific_evidence": False, "final_comparison_executed": False}


def prepare_inputs(development_rows, external_rows, *, allow_synthetic=False):
    """Separate history/development and sealed external inputs, without evaluating either."""
    history = validate_partition(development_rows, role="development_history", allow_synthetic=allow_synthetic)
    external = validate_partition(external_rows, role="external_evaluation", allow_synthetic=allow_synthetic)
    return {"development_history": history, "external_evaluation": external,
            "unobserved_periods": [{"start": "2025-01-01", "end": "2025-12-31", "quantity": None}],
            "status": "contract_validated_only", "final_comparison_executed": False}
