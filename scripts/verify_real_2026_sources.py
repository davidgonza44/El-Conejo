"""Read-only aggregate comparison of supplied real-2026 originals and frozen series."""
from collections import Counter, defaultdict
import argparse
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path


ORIGINALS = ("productos_2026.csv", "existencias_2026_final.csv",
             "movimientos_detalle_2026.csv", "demanda_limpia_2026.csv")
NORMALIZED = ("empirical_2026_build_report.json", "empirical_2026_cohort_manifest.csv",
              "empirical_2026_operational_calendar.csv", "empirical_2026_series_strict.csv",
              "empirical_2026_series_sensitivity.csv")
BUILD_SHA256 = "087bda535bc60f49f773d026434e10df5adcf5ce59fbc8669ffc861dd9ce82f8"
SIGNATURE = ("fecha_normalizada", "codigo_producto", "cantidad", "precio_unitario",
             "total_linea", "almacen_detalle")


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def quantity(value):
    return Decimal(value.replace(",", "."))


def receipt_check(folder):
    path = folder / "MANIFIESTO_SHA256.json"
    if not path.is_file():
        return {"provided": False}
    entries = json.loads(path.read_text(encoding="utf-8-sig"))
    valid = isinstance(entries, list) and len(entries) == len(ORIGINALS)
    checks = []
    seen = set()
    for item in entries if valid else []:
        name = item.get("archivo_del_zip")
        if name not in ORIGINALS or name in seen:
            valid = False
            continue
        seen.add(name)
        original = folder / name
        checks.append({"file": name, "bytes_match": original.stat().st_size == item.get("tamano_bytes"),
                       "sha_match": digest(original) == str(item.get("sha256", "")).lower()})
    return {"provided": True, "structure_valid": valid and seen == set(ORIGINALS), "files": checks}


def daily_check(normalized, demand, manifest):
    daily = defaultdict(Decimal)
    for row in demand:
        daily[row["codigo_producto"], row["fecha"]] += quantity(row["cantidad"])
    results = {}
    for cohort in ("STRICT", "SENSITIVITY"):
        codes = {code for code, item in manifest.items() if item["cohorte"] == cohort}
        seen = set()
        mismatches = duplicates = positive = 0
        filename = "empirical_2026_series_" + cohort.lower() + ".csv"
        for row in rows(normalized / filename):
            key = row["codigo_producto"], row["fecha_real"]
            duplicates += key in seen
            seen.add(key)
            amount = quantity(row["demanda_observada"])
            mismatches += amount != daily.get(key, Decimal(0))
            positive += amount > 0
        missing = sum(key[0] in codes and key not in seen for key in daily)
        results[cohort] = {"normalized_daily_rows": len(seen), "duplicate_daily_keys": duplicates,
                           "daily_decimal_mismatches": mismatches, "raw_positive_days_missing": missing,
                           "positive_daily_rows": positive}
    return results


def movement_check(demand, movements):
    by_detail = {row["id_detalle"]: row for row in movements}
    fields = {"id_movimiento": "id_movimiento", "numero_documento": "numero_movimiento",
              "codigo_producto": "codigo_producto", "nombre_producto": "nombre_producto",
              "cantidad": "cantidad", "precio_unitario": "precio_unitario", "total_linea": "total_linea",
              "fecha": "fecha_normalizada", "almacen": "almacen_detalle"}
    missing = mismatches = 0
    missing_dates = Counter()
    for row in demand:
        detail = by_detail.get(row["id_detalle"])
        if detail is None:
            missing += 1
            missing_dates[row["fecha"]] += 1
        else:
            mismatches += any(row[left] != detail[right] for left, right in fields.items())
    processed = [row for row in movements if row["estado"].upper() == "PROCESADO"]
    dispatch = {tuple(row[key] for key in SIGNATURE) for row in processed if row["tipo_movimiento"] == "2"}
    expected = {row["id_detalle"] for row in processed if row["tipo_movimiento"] == "2"
                or (row["tipo_movimiento"] == "1" and tuple(row[key] for key in SIGNATURE) not in dispatch)}
    actual = {row["id_detalle"] for row in demand}
    equivalents = sum(row["tipo_movimiento"] == "1" and tuple(row[key] for key in SIGNATURE) in dispatch
                      for row in processed)
    return {"missing_movement_detail": missing, "line_field_mismatches": mismatches,
            "missing_detail_date_counts": dict(sorted(missing_dates.items())),
            "missing_expected_canonical_ids": len(expected - actual), "extra_canonical_ids": len(actual - expected),
            "invoice_dispatch_equivalent_lines_excluded": equivalents,
            "movement_repeated_detail_ids": len(movements) - len(by_detail),
            "comparison_rule": "2025_signature_rule_diagnostic_not_a_redefinition_of_2026_canonical_demand"}


def unit_check(catalog, manifest, movements):
    units = defaultdict(set)
    for row in movements:
        units[row["codigo_producto"]].add(row["tipo_unidad"].strip())
    result = {}
    for cohort in ("STRICT", "SENSITIVITY", "QUARANTINE"):
        codes = {code for code, item in manifest.items() if item["cohorte"] == cohort}
        result[cohort] = {"products": len(codes),
                          "products_without_movement_unit_observation": sum(not units[code] for code in codes),
                          "products_with_multiple_movement_units": sum(len(units[code]) > 1 for code in codes),
                          "products_catalog_unit_not_among_movement_units": sum(
                              bool(units[code]) and catalog[code]["unidad_deventa_a"].strip() not in units[code]
                              for code in codes)}
    return result


def catalog_check(catalog_rows, catalog, manifest):
    return {"repeated_codes": len(catalog_rows) - len(catalog),
            "manifest_missing_catalog_codes": sum(code not in catalog for code in manifest),
            "manifest_name_mismatches": sum(catalog.get(code, {}).get("nombre") != item["nombre_actual"]
                                            for code, item in manifest.items()),
            "manifest_empty_catalog_unit": sum(not catalog.get(code, {}).get("unidad_deventa_a", "").strip()
                                               for code in manifest)}


def demand_coverage(demand):
    return {"start": min(row["fecha"] for row in demand), "end": max(row["fecha"] for row in demand),
            "unique_observed_dates": len({row["fecha"] for row in demand}),
            "products": len({row["codigo_producto"] for row in demand}),
            "documents": len({row["id_movimiento"] for row in demand})}


def demand_duplicates(demand):
    actual_ids = {row["id_detalle"] for row in demand}
    heuristic = Counter((row["fecha"], row["codigo_producto"], quantity(row["cantidad"])) for row in demand)
    return {"repeated_detail_ids": len(demand) - len(actual_ids),
            "exact_duplicate_rows": len(demand) - len({tuple(row.values()) for row in demand}),
            "date_product_quantity_collision_groups": sum(value > 1 for value in heuristic.values()),
            "collision_extra_rows": sum(value - 1 for value in heuristic.values() if value > 1)}


def comparison_status(result):
    source_match = (result["frozen_report_matches"] and all(result["normalized_artifacts_match"].values())
                    and all(item["equals_frozen"] for item in result["original_files"]))
    exact = all(item[key] == 0 for item in result["raw_to_normalized"].values() for key in (
        "duplicate_daily_keys", "daily_decimal_mismatches", "raw_positive_days_missing"))
    missing_audit = "movimientos_detalle_2026_auditoria_18092026.csv" in result["missing_originals"]
    return {"sources": "SAME_RESEARCH_SOURCES" if source_match else "DIFFERENT_SOURCES",
            "canonical_series": "CANONICAL_SERIES_EXACT" if exact else "CANONICAL_SERIES_DIFFER",
            "raw_traceability": "RAW_TRACE_LIMITED" if missing_audit else "RAW_TRACE_NOT_RECONSTRUCTED"}


def compare(source_directory, normalized_directory):
    folder, normalized = Path(source_directory), Path(normalized_directory)
    source_paths = {name: folder / name for name in ORIGINALS}
    before = {name: digest(path) for name, path in source_paths.items()}
    report = json.loads((normalized / NORMALIZED[0]).read_text(encoding="utf-8"))
    source_checks = [{"file": name, "bytes": path.stat().st_size, "sha256": before[name],
                      "expected_sha256": report["source_sha256_before"].get(name),
                      "equals_frozen": before[name] == report["source_sha256_before"].get(name)}
                     for name, path in source_paths.items()]
    artifact_checks = {name: digest(normalized / name) == report["artifact_sha256"].get(name)
                       for name in NORMALIZED[1:]}
    catalog_rows = list(rows(source_paths["productos_2026.csv"]))
    catalog = {row["codigo"]: row for row in catalog_rows}
    manifest = {row["codigo_producto"]: row for row in rows(normalized / NORMALIZED[1])}
    demand = list(rows(source_paths["demanda_limpia_2026.csv"]))
    movements = list(rows(source_paths["movimientos_detalle_2026.csv"]))
    inventory_count = sum(1 for _ in rows(source_paths["existencias_2026_final.csv"]))
    total_bytes = sum(path.stat().st_size for path in source_paths.values())
    total_bytes += sum((normalized / name).stat().st_size for name in NORMALIZED)
    result = {"original_files": source_checks, "receipt_manifest": receipt_check(folder),
              "frozen_report_matches": digest(normalized / NORMALIZED[0]) == BUILD_SHA256,
              "normalized_artifacts_match": artifact_checks, "unique_package_bytes": total_bytes,
              "rows": {"catalog": len(catalog_rows), "demand": len(demand), "movements": len(movements),
                       "inventory": inventory_count},
              "demand_coverage": demand_coverage(demand), "demand_duplicates": demand_duplicates(demand),
              "catalog_checks": catalog_check(catalog_rows, catalog, manifest),
              "raw_to_normalized": daily_check(normalized, demand, manifest),
              "demand_to_movements": movement_check(demand, movements),
              "unit_checks": unit_check(catalog, manifest, movements),
              "missing_originals": sorted(name for name in report["source_sha256_before"]
                                          if not (folder / name).is_file())}
    result["original_sources_unchanged"] = before == {name: digest(path) for name, path in source_paths.items()}
    result["status"] = comparison_status(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--normalized-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(compare(args.source_dir, args.normalized_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
