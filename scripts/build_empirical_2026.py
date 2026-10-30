"""Reproducible, data-only 2026 preparation. Never fits or evaluates models.

Run with --source-dir, --dictamen and --output-dir. Existing different outputs
are never overwritten. The closed semantic ruling is data, not instructions.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, timedelta
from decimal import Decimal, localcontext
import hashlib
import io
import json
import os
from pathlib import Path
import re
import statistics
import sys

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.demand_data_service import operational_points

START, END = date(2026, 1, 5), date(2026, 9, 18)
CLOSURES = set(map(date.fromisoformat, (
    "2026-01-10", "2026-02-16", "2026-02-17", "2026-04-02",
    "2026-04-03", "2026-04-04", "2026-05-01")))
OPENINGS = {date(2026, 5, 3), date(2026, 5, 17)}
EXTRAS = set("0716 0878 0887 1116 1388 1389 1858 1975 2183 3611".split())
SENSITIVITY = set((
    "0012 0083 0127 0129 0282 0439 0548 0594 0605 0640 0755 0788 0791 "
    "0847 0944 0981 1120 1123 1230 1314 1354 1418 1503 1619 1793 1995 "
    "1999 2004 2021 2023 2174 2222 2242 2262 2279 2393 2399 2406 2476 "
    "2579 3039 3085 3331 3332 3357").split())
SOURCES = ("productos_2026.csv", "movimientos_detalle_2026.csv",
           "movimientos_detalle_2026_auditoria_18092026.csv",
           "demanda_limpia_2026.csv", "existencias_2026_final.csv")
EXPECTED = {"STRICT": 2087, "SENSITIVITY": 45, "QUARANTINE": 407}
PREFIX = "empirical_2026_"
MANIFEST_FIELDS = ("codigo_producto", "nombre_actual", "cohorte", "estado_identidad",
                   "estado_unidad", "is_service", "incluido_pronostico",
                   "incluido_reabastecimiento", "motivo")
SERIES_FIELDS = ("codigo_producto", "nombre_actual", "cohorte", "is_service",
                 "fecha_real", "operational_index", "product_operational_index",
                 "demanda_observada", "es_cero_observado")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def hashes(paths):
    return {key: sha256(path.read_bytes()) for key, path in paths.items()}


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def csv_bytes(fields, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def parsed_csv(data):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))


def decimal_quantity(raw):
    require(isinstance(raw, str) and re.fullmatch(r"\d+(?:[.,]\d{1,6})?", raw),
            f"Invalid canonical quantity: {raw!r}")
    amount = Decimal(raw.replace(",", "."))
    require(amount.is_finite() and amount > 0, f"Non-positive quantity: {raw!r}")
    return amount


def business_calendar():
    """User-confirmed rule, independent of observed dates."""
    days = [START + timedelta(days=i) for i in range((END - START).days + 1)]
    return [day for day in days if (day.weekday() != 6 and day not in CLOSURES)
            or day in OPENINGS]


def validate_calendar(calendar, observed):
    missing, additional = set(calendar) - set(observed), set(observed) - set(calendar)
    require(calendar == business_calendar() and len(calendar) == 216,
            "Calendar does not match the 216-day confirmed business rule")
    require(not missing and not additional,
            f"Calendar-only={sorted(map(str, missing))}; canonical-only={sorted(map(str, additional))}")
    return {"operational_dates": 216, "calendar_only": [], "canonical_only": []}


def table_rows(text, start, end):
    require(text.count(start) == 1, f"Ambiguous or missing ruling section: {start}")
    section = text.split(start, 1)[1].split(end, 1)
    require(len(section) == 2, f"Missing ruling section end: {end}")
    rows = []
    for line in section[0].splitlines():
        match = re.match(r"^\|\s*`([^`]+)`\s*\|", line)
        if match:
            rows.append((match.group(1), line))
    duplicates = [code for code, count in Counter(code for code, _ in rows).items() if count > 1]
    require(not duplicates, f"Duplicate ruling codes: {duplicates}")
    return rows


def parse_ruling(text):
    changed = dict(table_rows(text, "## 3.2 Lista completa", "### Ejemplos"))
    review = dict(table_rows(text, "## 3.3 Lista completa", "# 4. Elegibilidad de unidades"))
    units = dict(table_rows(text, "## 4.2 Perfiles", "## 4.3"))
    alerts = dict(table_rows(text, "## 4.3 Cuatro alertas", "# 5."))
    require(len(changed) == 53 and len(review) == 344,
            f"Identity counts: changed={len(changed)}, review={len(review)}; codes={sorted(changed | review)}")
    require(not (changed.keys() & review.keys()),
            f"Identity overlap: {sorted(changed.keys() & review.keys())}")
    require(len(units) == 62 and set(alerts) == {"0878", "1116", "1388", "1858"},
            f"Unit metadata mismatch: units={sorted(units)}, alerts={sorted(alerts)}")
    classes = {code: line.rstrip().rstrip("|").split("|")[-1].strip()
               for code, line in units.items()}
    require(Counter(classes.values()) == Counter({"T": 52, "N": 9, "M": 1}),
            f"Unit classes mismatch: {classes}")
    return set(changed), set(review), classes, set(alerts)


def partition(universe, changed, review, extras, sensitivity, expected):
    groups = [changed, review, extras, sensitivity]
    duplicates = {code for i, group in enumerate(groups) for other in groups[i + 1:]
                  for code in group & other}
    unknown = set().union(*groups) - universe
    require(not duplicates and not unknown,
            f"Cohort overlaps={sorted(duplicates)}; unknown={sorted(unknown)}")
    quarantine = changed | review | extras
    result = {"STRICT": universe - quarantine - sensitivity,
              "SENSITIVITY": sensitivity, "QUARANTINE": quarantine}
    counts = {key: len(value) for key, value in result.items()}
    require(counts == expected and sum(counts.values()) == len(universe),
            f"Cohort counts={counts}; expected={expected}; memberships={ {k: sorted(v) for k, v in result.items()} }")
    return result


def make_manifest(catalog, cohorts, changed, review, units, alerts):
    rows = []
    unit_names = {"T": "VARIANTE_TEXTUAL_PLAUSIBLE", "N": "MULTIUNIDAD_NO_RESUELTA",
                  "M": "MULTICANT_ESPECIAL"}
    for cohort, codes in cohorts.items():
        for code in sorted(codes):
            identity = ("CAMBIO_CLARO_DE_ARTICULO" if code in changed else
                        "REVISAR_IDENTIDAD" if code in review else "CONTINUIDAD_PLAUSIBLE")
            unit = unit_names.get(units.get(code), "UNIDAD_SIMPLE")
            reasons = [identity] if identity != "CONTINUIDAD_PLAUSIBLE" else []
            if unit in {"MULTIUNIDAD_NO_RESUELTA", "MULTICANT_ESPECIAL"}:
                reasons.append(unit)
            if code in alerts or code == "0716":
                reasons.append("UNIDAD_PRESENTACION_PENDIENTE")
            if code == "0716":
                reasons.append("DOCUMENTO_INTEGRIDAD_PENDIENTE")
            included = cohort != "QUARANTINE"
            rows.append(dict(zip(MANIFEST_FIELDS, (
                code, catalog[code], cohort, identity, unit, str(code == "0808").lower(),
                str(included).lower(), str(included and code != "0808").lower(),
                ";".join(reasons) or "sin motivo semántico pendiente"))))
    return sorted(rows, key=lambda row: row["codigo_producto"])


def canonical_input(rows):
    observations = defaultdict(list)
    for row in rows:
        code = row["codigo_producto"]
        require(code and code == code.strip(), f"Invalid text product code: {code!r}")
        day = date.fromisoformat(row["fecha"])
        require(START <= day <= END, f"Out-of-period observation: {code}/{day}")
        observations[code].append((day, decimal_quantity(row["cantidad"])))
    return dict(observations)


def make_series(manifest, observations, calendar, cohort):
    indices = {day: index for index, day in enumerate(calendar, 1)}
    rows = []
    for product in manifest:
        if product["cohorte"] != cohort:
            continue
        code = product["codigo_producto"]
        points = operational_points(observations[code], calendar, end=END)
        for index, point in enumerate(points, 1):
            rows.append(dict(zip(SERIES_FIELDS, (
                code, product["nombre_actual"], cohort, product["is_service"],
                point.day.isoformat(), str(indices[point.day]), str(index),
                format(point.demand, "f"), str(point.demand == 0).lower()))))
    return rows


def validate_series(rows, manifest, observations, calendar, cohort):
    """Independent exact readback, not an invocation of the construction helper."""
    products = {row["codigo_producto"]: row for row in manifest if row["cohorte"] == cohort}
    actual, grouped = {}, defaultdict(list)
    indices = {day: index for index, day in enumerate(calendar, 1)}
    order = [(row["codigo_producto"], row["fecha_real"]) for row in rows]
    require(order == sorted(order), f"Unordered {cohort} rows")
    for row in rows:
        code, day = row["codigo_producto"], date.fromisoformat(row["fecha_real"])
        require(code in products, f"Unknown {cohort} series code: {code}")
        key = code, day
        require(key not in actual, f"Duplicate series key: {code}/{day}")
        amount = Decimal(row["demanda_observada"])
        require(amount.is_finite() and amount >= 0, f"Invalid series quantity: {code}/{day}")
        require(day in indices, f"Non-operational series date: {code}/{day}")
        require(row["operational_index"] == str(indices[day]), f"Global index: {code}/{day}")
        require(all(row[field] == products[code][field] for field in
                    ("nombre_actual", "cohorte", "is_service")), f"Metadata mismatch: {code}")
        require(row["es_cero_observado"] == str(amount == 0).lower(), f"Zero flag: {code}/{day}")
        actual[key] = amount
        grouped[code].append((day, amount, row["product_operational_index"]))
    require(set(grouped) == set(products), f"Missing products: {sorted(set(products) - set(grouped))}")
    expected = defaultdict(Decimal)
    for code in products:
        for day, amount in observations[code]:
            expected[code, day] += amount
    expected_positive = {key for key, amount in expected.items() if amount > 0}
    actual_positive = {key for key, amount in actual.items() if amount > 0}
    missing, additional = expected_positive - actual_positive, actual_positive - expected_positive
    require(not missing and not additional, f"Positive keys missing={sorted(missing)}, additional={sorted(additional)}")
    differences = []
    for code, points in grouped.items():
        first = min(day for day, amount in observations[code] if amount > 0)
        require([day for day, _, _ in points] == [day for day in calendar if day >= first],
                f"Left-censoring or incomplete window: {code}")
        require([index for _, _, index in points] == list(map(str, range(1, len(points) + 1))),
                f"Product index: {code}")
        for day, amount, _ in points:
            require(amount == expected.get((code, day), Decimal(0)), f"Daily quantity differs: {code}/{day}")
        difference = abs(sum((value for _, value, _ in points), Decimal(0)) -
                         sum((value for _, value in observations[code]), Decimal(0)))
        if difference:
            differences.append({"codigo_producto": code, "difference": str(difference)})
    require(not differences, f"Per-product quantity differences: {differences}")
    return {"products_with_quantity_discrepancies": differences, "maximum_absolute_error": "0",
            "positive_missing": 0, "positive_additional": 0, "left_censoring_violations": 0,
            "non_operational_dates": 0, "invalid_quantities": 0, "duplicate_product_dates": 0}


def statistics_for(rows):
    sizes, positive = Counter(), Counter()
    for row in rows:
        code = row["codigo_producto"]
        sizes[code] += 1
        positive[code] += Decimal(row["demanda_observada"]) > 0
    positives = sum(positive.values())
    summary = lambda values: {"min": min(values), "median": statistics.median(values), "max": max(values)}
    dates = [row["fecha_real"] for row in rows]
    return {"products": len(sizes), "rows": len(rows), "positive_rows": positives,
            "zero_rows": len(rows) - positives,
            "zero_percentage": str(Decimal(100) * (len(rows) - positives) / len(rows)),
            "first_date": min(dates), "last_date": max(dates),
            "observations_per_product": summary(list(sizes.values())),
            "positive_days_per_product": summary(list(positive.values()))}


def write_new_or_identical(output, artifacts):
    """Fail before any output mutation if an existing artifact differs."""
    for name, data in artifacts.items():
        target = output / name
        require(not target.is_symlink(), f"Output symlink refused: {name}")
        require(not target.exists() or target.read_bytes() == data, f"Existing output differs: {name}")
    output.mkdir(parents=True, exist_ok=True)
    for name, data in artifacts.items():
        target = output / name
        if not target.exists():
            with target.open("xb") as handle:
                handle.write(data)
        require(target.read_bytes() == data, f"Written artifact readback differs: {name}")


def build(source_dir, dictamen, output):
    paths = {name: source_dir / name for name in SOURCES}
    paths["auditoria_elegibilidad_semantica_2026.md"] = dictamen
    before = hashes(paths)
    calendar = business_calendar()
    raw = read_csv(paths["demanda_limpia_2026.csv"])
    observations = canonical_input(raw)
    require(len(raw) == 57683 and len({row["id_movimiento"] for row in raw}) == 30956,
            "Canonical line/document counts differ")
    require(len(observations) == 2539, f"Canonical universe size: {len(observations)}")
    calendar_check = validate_calendar(calendar, {day for values in observations.values() for day, _ in values})
    changed, review, units, alerts = parse_ruling(dictamen.read_text(encoding="utf-8-sig"))
    cohorts = partition(set(observations), changed, review, EXTRAS, SENSITIVITY, EXPECTED)
    require(not (set(units) - set(observations)), f"Unknown unit codes: {sorted(set(units) - set(observations))}")
    unit_sensitivity = {code for code, state in units.items() if state == "T"} - cohorts["QUARANTINE"]
    require(unit_sensitivity == SENSITIVITY,
            f"Sensitivity unit mismatch: missing={sorted(SENSITIVITY - unit_sensitivity)}, additional={sorted(unit_sensitivity - SENSITIVITY)}")
    catalog_rows = read_csv(paths["productos_2026.csv"])
    catalog = {row["codigo"]: row["nombre"] for row in catalog_rows}
    require(len(catalog) == len(catalog_rows), "Duplicate catalog codes")
    require(not (set(observations) - set(catalog)), f"Unknown catalog codes: {sorted(set(observations) - set(catalog))}")
    manifest = make_manifest(catalog, cohorts, changed, review, units, alerts)
    invalid_strict = [row["codigo_producto"] for row in manifest if row["cohorte"] == "STRICT"
                      and (row["estado_unidad"] != "UNIDAD_SIMPLE" or
                           row["motivo"] != "sin motivo semántico pendiente")]
    require(not invalid_strict, f"Strict metadata contradicts closed membership: {invalid_strict}")
    manifest_data = csv_bytes(MANIFEST_FIELDS, manifest)
    require(parsed_csv(manifest_data) == manifest, "Manifest text readback differs")
    calendar_rows = [{"fecha_real": str(day), "operational_index": index,
                      "tipo_dia": "DIA_OPERATIVO_EXCEPCIONAL_CONFIRMADO" if day in OPENINGS else "DIA_OPERATIVO_NORMAL",
                      "evidencia": "Confirmación empresarial comunicada por el usuario: Rafael Sánchez"}
                     for index, day in enumerate(calendar, 1)]
    artifacts = {PREFIX + "cohort_manifest.csv": manifest_data,
                 PREFIX + "operational_calendar.csv": csv_bytes(tuple(calendar_rows[0]), calendar_rows)}
    checks, summaries = {}, {}
    for cohort in ("STRICT", "SENSITIVITY"):
        data = csv_bytes(SERIES_FIELDS, make_series(manifest, observations, calendar, cohort))
        readback = parsed_csv(data)
        checks[cohort] = validate_series(readback, manifest, observations, calendar, cohort)
        summaries[cohort] = statistics_for(readback)
        artifacts[PREFIX + "series_" + cohort.lower() + ".csv"] = data
    after = hashes(paths)
    require(before == after, "Source integrity changed during build")
    report = {"READY_FOR_BACKTEST": True, "models_executed": False, "backtesting_executed": False,
              "source_sha256_before": before, "source_sha256_after": after,
              "builder_sha256": sha256(Path(__file__).read_bytes()),
              "operational_helper_sha256": sha256((Path(__file__).resolve().parents[1] / "app/services/demand_data_service.py").read_bytes()),
              "artifact_sha256": {name: sha256(data) for name, data in artifacts.items()},
              "calendar": calendar_check, "cohort_counts": EXPECTED,
              "canonical": {"lines": len(raw), "documents": len({row["id_movimiento"] for row in raw}),
                            "products": len(observations)},
              "cohort_partition": {"unknown_codes": [], "overlapping_codes": [], "exhaustive": True},
              "validation": checks, "statistics": summaries,
              "protocol": {"start": str(START), "cutoff": str(END), "normal_closed_weekday": "Sunday",
                           "extraordinary_closures": sorted(map(str, CLOSURES)),
                           "confirmed_exceptional_openings": sorted(map(str, OPENINGS)),
                           "calendar_evidence": "User-provided business confirmation from Rafael Sánchez; not inferred from movements",
                           "quantity": "Original registered quantity; no MULTICANT transformation",
                           "prehistory": "LEFT-CENSORED / NO OBSERVADO; no rows before first positive demand",
                           "zero": "Observed demand zero; no claim about latent demand or stock availability",
                           "service_overrides": {"0808": {"is_service": True, "incluido_reabastecimiento": False}}},
              "schema": {"codigo_producto": "TEXT; leading zeros preserved; import CSV as text",
                         "demanda_observada": "Exact Decimal, base 10, dot separator",
                         "operational_index": "Global 1-based calendar position",
                         "product_operational_index": "Product 1-based observation position"}}
    artifacts[PREFIX + "build_report.json"] = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    write_new_or_identical(output, artifacts)
    require(hashes(paths) == before, "Source integrity changed after output readback")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--dictamen", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    with localcontext() as context:
        context.prec = 50
        report = build(args.source_dir, args.dictamen, args.output_dir)
    print(json.dumps({"READY_FOR_BACKTEST": report["READY_FOR_BACKTEST"],
                      "cohort_counts": report["cohort_counts"], "statistics": report["statistics"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
