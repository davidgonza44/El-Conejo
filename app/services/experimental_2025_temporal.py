"""Read verified 2026 STRICT inputs without changing their frozen experiment."""
from collections import defaultdict
import csv
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import unicodedata

BUILD_SHA256 = "087bda535bc60f49f773d026434e10df5adcf5ce59fbc8669ffc861dd9ce82f8"
FILES = ("empirical_2026_cohort_manifest.csv", "empirical_2026_operational_calendar.csv",
         "empirical_2026_series_strict.csv")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def csv_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def normalized_name(value):
    return " ".join(unicodedata.normalize("NFC", value).upper().split())


def load_verified_2026(directory):
    directory = Path(directory)
    report_path = directory / "empirical_2026_build_report.json"
    if digest(report_path) != BUILD_SHA256:
        raise ValueError("Frozen 2026 construction manifest changed")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not report["READY_FOR_BACKTEST"]:
        raise ValueError("2026 construction not ready")
    if report["source_sha256_before"] != report["source_sha256_after"]:
        raise ValueError("2026 source integrity mismatch")
    hashes = {name: digest(directory / name) for name in FILES}
    if any(value != report["artifact_sha256"][name] for name, value in hashes.items()):
        raise ValueError("Frozen 2026 normalized input changed")
    calendar_rows = csv_rows(directory / FILES[1])
    calendar = [date.fromisoformat(row["fecha_real"]) for row in calendar_rows]
    if len(calendar) != 216 or calendar != sorted(set(calendar)):
        raise ValueError("Invalid frozen operational calendar")
    manifest = {row["codigo_producto"]: row for row in csv_rows(directory / FILES[0])
                if row["cohorte"] == "STRICT"}
    if len(manifest) != 2087:
        raise ValueError("Frozen STRICT universe changed")
    grouped = defaultdict(list)
    for row in csv_rows(directory / FILES[2]):
        code = row["codigo_producto"]
        day, quantity = date.fromisoformat(row["fecha_real"]), float(row["demanda_observada"])
        if code not in manifest or not math.isfinite(quantity) or quantity < 0:
            raise ValueError("Invalid 2026 observation")
        if row["cohorte"] != "STRICT" or int(row["product_operational_index"]) != len(grouped[code]) + 1:
            raise ValueError("Invalid cohort/index")
        if calendar[int(row["operational_index"]) - 1] != day:
            raise ValueError("Operational date/index mismatch")
        grouped[code].append({"date": day, "quantity": quantity,
                              "source_id": FILES[2], "data_kind": "real_observed", "year": 2026,
                              "observation_basis": "verified_frozen_operational_suffix"})
    for code, points in grouped.items():
        if points[0]["quantity"] <= 0 or [p["date"] for p in points] != [
            day for day in calendar if day >= points[0]["date"]
        ]:
            raise ValueError("2026 left-censoring/calendar violation")
    if set(grouped) != set(manifest):
        raise ValueError("Missing STRICT product")
    return {"observations": dict(grouped), "manifest": manifest, "calendar": calendar,
            "input_sha256": {**hashes, report_path.name: BUILD_SHA256},
            "metadata": {"year": 2026, "data_kind": "real_observed",
                         "purpose": "experimental_evaluation", "cohort": "STRICT",
                         "time_unit": "operational_day", "source_id": FILES[2],
                         "range": [calendar[0].isoformat(), calendar[-1].isoformat()],
                         "raw_sources_reaudited": False}}


def identity_bridge(bundle, external, *, external_units=None):
    """Exact code + normalized demand name + explicit unit; no renumbering."""
    source = bundle["product_identity_by_code"]
    bridge, reasons = {}, defaultdict(int)
    external_units = external_units or {}
    for code, product in external["manifest"].items():
        if product["is_service"].lower() == "true":
            reasons["service_excluded"] += 1
        elif code not in source:
            reasons["no_2025_code"] += 1
        elif normalized_name(source[code]["name"]) != normalized_name(product["nombre_actual"]):
            reasons["cross_year_name_mismatch"] += 1
        elif code not in external_units:
            reasons["cross_year_unit_unverified"] += 1
        elif source[code]["unit"] != external_units[code]:
            reasons["cross_year_unit_mismatch"] += 1
        else:
            bridge[code] = {"match": "exact_code_normalized_name_exact_unit",
                            "unit": external_units[code]}
    return bridge, dict(reasons)


def load_external_units(source_directory, build_directory, *, catalog_path=None):
    """Only explicitly supplied, frozen source; never discovers private files."""
    report = json.loads((Path(build_directory) / "empirical_2026_build_report.json").read_text(encoding="utf-8"))
    if catalog_path is not None:
        path = Path(catalog_path)
        if digest(path) != report["source_sha256_before"]["productos_2026.csv"]:
            raise ValueError("2026 catalog differs from audited frozen source")
        # Only STRICT UNIDAD_SIMPLE is considered by identity_bridge. The
        # frozen semantic audit rules out unit ambiguity for those products.
        units = {row["codigo"]: row["unidad_deventa_a"].strip() for row in csv_rows(path)}
        return units, {"available": True, "source_id": "productos_2026.csv", "sha256": digest(path),
                       "unit_basis": "catalog_unit_A_and_frozen_STRICT_simple_unit_audit",
                       "stable_unit_products": len(units)}
    if source_directory is None:
        return {}, {"available": False, "reason": "2026_source_directory_not_supplied"}
    path = Path(source_directory) / "movimientos_detalle_2026.csv"
    if digest(path) != report["source_sha256_before"][path.name]:
        raise ValueError("2026 detail source differs from audited frozen input")
    units = defaultdict(set)
    for row in csv_rows(path):
        units[row["codigo_producto"]].add(row["tipo_unidad"].strip())
    valid = {code: next(iter(values)) for code, values in units.items() if len(values) == 1}
    return valid, {"available": True, "source_id": path.name, "sha256": digest(path),
                   "stable_unit_products": len(valid), "unstable_unit_products": len(units) - len(valid)}


def scenario_panel(bundle, *, activity_calendar=False):
    """Explicit synthetic completeness scenario; absent SKU is an assumed zero."""
    grouped = defaultdict(lambda: defaultdict(float))
    for row in bundle["observations"]:
        grouped[row["product_code"]][date.fromisoformat(row["date"])] += row["quantity"]
    dates = sorted({day for rows in grouped.values() for day in rows})
    if not activity_calendar:
        from datetime import timedelta
        dates = [dates[0] + timedelta(days=index) for index in range((dates[-1] - dates[0]).days + 1)]
    output = {}
    for code, quantities in sorted(grouped.items()):
        first = min(quantities)
        output[code] = [{"date": day, "quantity": quantities.get(day, 0.0),
                         "source_id": "Demanda_limpia_2025_CORREGIDA_FINAL.csv",
                         "data_kind": "synthetic_structured", "year": 2025,
                         "observation_basis": ("source_positive_line" if day in quantities else
                                               "assumed_zero_synthetic_completeness_scenario")}
                        for day in dates if day >= first]
    return output, dates
