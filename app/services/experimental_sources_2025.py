"""Read-only, stdlib adapter for the explicitly authorized structured 2025 sources.

No Flask, model, database, filesystem writes or implicit calendar completion.
Identifiers and source amounts remain unchanged; source paths are never returned.
"""
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import math
from pathlib import Path
import re


METADATA = {
    "data_kind": "synthetic_structured", "year": 2025,
    "purpose": "experimental_evaluation", "label": "EVALUACIÓN EXPERIMENTAL 2025",
}
SCHEMAS = {
    "demand": "fecha id_movimiento fuente_demanda numero_documento id_detalle "
              "codigo_producto nombre_producto cantidad precio_unitario total_linea almacen",
    "movements": "fecha_original fecha_normalizada id_movimiento tipo_movimiento "
                 "numero_movimiento estado almacen_movimiento almacen_destino items_documento "
                 "total_documento id_detalle codigo_producto nombre_producto cantidad "
                 "precio_unitario total_linea porcentaje_iva exento tipo_unidad almacen_detalle "
                 "almacen_destino_detalle costo_bruto_unitario",
    "inventory": "id_producto estado_producto id_almacen codigo_producto nombre_producto "
                 "stock_actual unidad fecha_ultimo_movimiento hora_ultimo_movimiento "
                 "ultima_accion ultimo_modulo ultimo_documento estado_kardex",
}
SIGNATURE = ("fecha_normalizada", "codigo_producto", "cantidad", "precio_unitario",
             "total_linea", "almacen_detalle")
NUMERIC = {
    "demand": ("cantidad", "precio_unitario", "total_linea"),
    "movements": ("cantidad", "precio_unitario", "total_linea", "total_documento",
                  "items_documento", "porcentaje_iva", "costo_bruto_unitario"),
    "inventory": ("stock_actual",),
}
CATEGORIES = {
    "demand": ("fuente_demanda", "almacen"),
    "movements": ("tipo_movimiento", "estado", "almacen_movimiento", "almacen_detalle",
                  "tipo_unidad"),
    "inventory": ("estado_producto", "estado_kardex", "id_almacen", "unidad"),
}


class DatasetAuditError(ValueError):
    """Invalid dataset; aggregate audit is accessible without exposing source rows."""

    def __init__(self, audit):
        self.audit = audit
        super().__init__("2025 dataset audit failed: " + ", ".join(audit["fatal_reasons"]))


def _decimal(value):
    if not re.fullmatch(r"[+-]?\d+(?:[,.]\d+)?", value):
        raise ValueError("invalid_numeric_format")
    try:
        number = Decimal(value.replace(",", "."))
    except InvalidOperation as error:
        raise ValueError("invalid_number") from error
    if not number.is_finite() or not math.isfinite(float(number)):
        raise ValueError("nonfinite_number")
    return number


def _date(value, *, local=False):
    if local:
        return datetime.strptime(value, "%d/%m/%Y").date()
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("noncanonical_date")
    return parsed


def _read(path, role):
    source = Path(path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        missing = sorted(set(SCHEMAS[role].split()) - set(headers))
        if missing or len(headers) != len(set(headers)):
            raise DatasetAuditError({"fatal_reasons": [role + "_invalid_headers"],
                                     "missing_columns": missing})
        rows = list(reader)
    if not rows or any(None in row or any(value is None for value in row.values()) for row in rows):
        raise DatasetAuditError({"fatal_reasons": [role + "_empty_or_malformed_csv"]})
    return rows, {"source_id": source.name, "sha256": digest, "columns": headers,
                  "row_count": len(rows), "encoding": "utf-8-sig", "delimiter": ","}


def _numeric_summary(rows, key):
    values, invalid = [], 0
    for row in rows:
        if not row[key]:
            continue
        try:
            values.append(_decimal(row[key]))
        except ValueError:
            invalid += 1
    return {
        "invalid_count": invalid, "null_count": sum(not r[key] for r in rows),
        "negative_count": sum(v < 0 for v in values), "zero_count": sum(v == 0 for v in values),
        "positive_count": sum(v > 0 for v in values),
        "minimum": float(min(values)) if values else None,
        "maximum": float(max(values)) if values else None,
        "sum": float(sum(values)) if values else None,
        "sum_interpretation": "arithmetic_only_mixed_units_not_homogeneous_volume",
    }


def _date_summary(rows, key, *, local=False):
    dates, invalid = [], 0
    for row in rows:
        if not row[key]:
            continue
        try:
            dates.append(_date(row[key], local=local))
        except ValueError:
            invalid += 1
    return {"invalid_count": invalid, "null_count": sum(not r[key] for r in rows),
            "minimum": min(dates).isoformat() if dates else None,
            "maximum": max(dates).isoformat() if dates else None,
            "future_to_snapshot_count": sum(day > date(2025, 12, 31) for day in dates),
            "outside_2025_count": sum(day.year != 2025 for day in dates)}


def _name_summary(rows):
    names = defaultdict(set)
    for row in rows:
        names[row["codigo_producto"]].add(row["nombre_producto"])
    return {"distinct_products": len(names), "products_with_multiple_names": sum(len(v) > 1 for v in names.values()),
            "empty_code_count": sum(not r["codigo_producto"] for r in rows),
            "empty_name_count": sum(not r["nombre_producto"] for r in rows),
            "codes_with_initial_zero": sum(code.startswith("0") for code in names),
            "replacement_character_row_count": sum(any("\ufffd" in v for v in r.values()) for r in rows)}


def _audit_source(rows, provenance, role):
    result = {**provenance, "null_count_by_column": {k: sum(not row[k] for row in rows) for k in provenance["columns"]},
              "exact_duplicate_row_count": len(rows) - len({tuple(row.values()) for row in rows}),
              "numeric": {k: _numeric_summary(rows, k) for k in NUMERIC[role]},
              "categories": {k: dict(sorted(Counter(row[k] for row in rows).items())) for k in CATEGORIES[role]},
              "identity": _name_summary(rows)}
    if role == "inventory":
        result["dates"] = _date_summary(rows, "fecha_ultimo_movimiento", local=True)
        result["duplicate_product_row_count"] = len(rows) - result["identity"]["distinct_products"]
        result["snapshot_structure"] = _snapshot_structure(rows)
        return result
    result["dates"] = _date_summary(rows, "fecha" if role == "demand" else "fecha_normalizada")
    for key in ("id_detalle", "id_movimiento"):
        result[key + "_unique_count"] = len({row[key] for row in rows})
        result[key + "_empty_count"] = sum(not row[key] for row in rows)
    result["duplicate_detail_id_count"] = len(rows) - result["id_detalle_unique_count"]
    result["document_count"] = len({row["numero_documento" if role == "demand" else "numero_movimiento"] for row in rows})
    result["line_arithmetic"] = _line_arithmetic(rows)
    if role == "movements":
        result["original_dates"] = _date_summary(rows, "fecha_original", local=True)
        result["document_structure"] = _document_structure(rows)
    return result


def _line_arithmetic(rows):
    errors = []
    for row in rows:
        try:
            quantity, price, total = (_decimal(row[k]) for k in ("cantidad", "precio_unitario", "total_linea"))
        except ValueError:
            continue
        error = abs(quantity * price - total)
        if error > Decimal("0.011"):
            errors.append((error, error / max(abs(total), Decimal(1))))
    return {"tolerance": 0.011, "mismatch_count": len(errors),
            "maximum_absolute_difference": float(max((e[0] for e in errors), default=0)),
            "maximum_relative_difference": float(max((e[1] for e in errors), default=0)),
            "action": "preserve_source_values_exclude_monetary_evaluation"}


def _document_structure(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["id_movimiento"]].append(row)
    metadata_keys = ("numero_movimiento", "tipo_movimiento", "estado", "fecha_normalizada",
                     "almacen_movimiento", "items_documento", "total_documento")
    inconsistent = sum(any(len({row[key] for row in group}) > 1 for key in metadata_keys) for group in groups.values())
    item_mismatches = 0
    for group in groups.values():
        try:
            item_mismatches += _decimal(group[0]["items_documento"]) != len(group)
        except ValueError:
            item_mismatches += 1
    date_mismatches = 0
    for row in rows:
        try:
            date_mismatches += _date(row["fecha_original"], local=True).isoformat() != row["fecha_normalizada"]
        except ValueError:
            date_mismatches += 1
    return {"movement_count": len(groups), "inconsistent_metadata_count": inconsistent,
            "declared_item_count_mismatch": item_mismatches,
            "original_normalized_date_mismatch": date_mismatches}


def _snapshot_structure(rows):
    invalid_time = 0
    for row in rows:
        value = row["hora_ultimo_movimiento"].strip()
        if not value:
            continue
        pattern = "%I:%M:%S %p" if value.endswith(("AM", "PM")) else "%H:%M:%S"
        try:
            datetime.strptime(value, pattern)
        except ValueError:
            invalid_time += 1
    return {"invalid_last_movement_time_count": invalid_time,
            "unknown_stock_without_missing_kardex_count": sum(not r["stock_actual"] and r["estado_kardex"] != "SIN_REGISTRO_KARDEX" for r in rows),
            "missing_kardex_with_numeric_stock_count": sum(bool(r["stock_actual"]) and r["estado_kardex"] == "SIN_REGISTRO_KARDEX" for r in rows)}


def _cross_audit(demand, movements, inventory):
    by_detail = {r["id_detalle"]: r for r in movements}
    by_code = {r["codigo_producto"]: r for r in inventory}
    mismatches = Counter()
    fields = {"id_movimiento": "id_movimiento", "numero_documento": "numero_movimiento",
              "codigo_producto": "codigo_producto", "nombre_producto": "nombre_producto",
              "cantidad": "cantidad", "precio_unitario": "precio_unitario", "total_linea": "total_linea",
              "fecha": "fecha_normalizada", "almacen": "almacen_detalle"}
    for row in demand:
        detail = by_detail.get(row["id_detalle"])
        mismatches["missing_detail"] += detail is None
        mismatches["missing_inventory_product"] += row["codigo_producto"] not in by_code
        if detail:
            for left, right in fields.items():
                mismatches[left + "_mismatch"] += row[left] != detail[right]
            expected = "DESPACHO" if detail["tipo_movimiento"] == "2" else "FACTURA_SIN_DESPACHO_EQUIVALENTE"
            mismatches["source_type_mismatch"] += row["fuente_demanda"] != expected
    processed = [r for r in movements if r["estado"].upper() == "PROCESADO"]
    dispatch = {tuple(r[k] for k in SIGNATURE) for r in processed if r["tipo_movimiento"] == "2"}
    expected_ids = {r["id_detalle"] for r in processed if r["tipo_movimiento"] == "2" or
                    (r["tipo_movimiento"] == "1" and tuple(r[k] for k in SIGNATURE) not in dispatch)}
    selected = {r["id_detalle"] for r in demand}
    units = defaultdict(set)
    for row in movements:
        units[row["codigo_producto"]].add(row["tipo_unidad"])
    shared = [(r, by_code[r["codigo_producto"]]) for r in movements if r["codigo_producto"] in by_code]
    return {"row_field_mismatches": dict(sorted(mismatches.items())),
            "expected_demand_detail_count": len(expected_ids), "actual_demand_detail_count": len(selected),
            "missing_expected_detail_count": len(expected_ids - selected), "unexpected_detail_count": len(selected - expected_ids),
            "excluded_rows": len(movements) - len(demand),
            "excluded_state_type": [{"state": a, "type": b, "count": n} for (a, b), n in sorted(Counter((r["estado"], r["tipo_movimiento"]) for r in movements if r["id_detalle"] not in selected).items())],
            "invoice_dispatch_equivalent_count": sum(r["tipo_movimiento"] == "1" and tuple(r[k] for k in SIGNATURE) in dispatch for r in processed),
            "equivalence_rule": list(SIGNATURE), "equivalence_limit": "row_signature_consistency_not_real_document_linkage",
            "snapshot_name_mismatch_product_count": len({r["codigo_producto"] for r, s in shared if r["nombre_producto"] != s["nombre_producto"]}),
            "snapshot_name_mismatch_row_count": sum(r["nombre_producto"] != s["nombre_producto"] for r, s in shared),
            "snapshot_unit_mismatch_product_count": len({r["codigo_producto"] for r, s in shared if r["tipo_unidad"] != s["unidad"]}),
            "multiple_demand_units_product_count": sum(len(v) > 1 for v in units.values())}


def _source_failures(role, source):
    identity = source["identity"]
    checks = {
        "invalid_dates": source["dates"]["invalid_count"],
        "invalid_or_negative_numbers": any(v["invalid_count"] or v["negative_count"] for v in source["numeric"].values()),
        "unstable_identity": any(identity[k] for k in ("empty_code_count", "empty_name_count", "products_with_multiple_names")),
        "duplicate_identity": source.get("duplicate_detail_id_count", source.get("duplicate_product_row_count", 0)),
    }
    if role == "inventory":
        checks["inconsistent_snapshot_structure"] = any(source["snapshot_structure"].values())
    else:
        checks.update(outside_2025=source["dates"]["outside_2025_count"], missing_date=source["dates"]["null_count"],
                      missing_or_nonpositive_quantity=source["numeric"]["cantidad"]["null_count"] + source["numeric"]["cantidad"]["zero_count"],
                      missing_monetary_fields=any(source["numeric"][k]["null_count"] for k in ("precio_unitario", "total_linea")),
                      empty_ids=source["id_detalle_empty_count"] + source["id_movimiento_empty_count"])
    return [role + "_" + reason for reason, failed in checks.items() if failed]


def _gate(audit):
    fatal = [reason for role, source in audit["sources"].items() for reason in _source_failures(role, source)]
    warnings = [role + "_line_arithmetic_mismatch" for role, source in audit["sources"].items() if source.get("line_arithmetic", {}).get("mismatch_count")]
    structure = audit["sources"]["movements"]["document_structure"]
    if structure["inconsistent_metadata_count"] or structure["original_normalized_date_mismatch"]:
        fatal.append("movement_inconsistent_metadata_or_dates")
    if structure["declared_item_count_mismatch"]:
        warnings.append("movement_declared_item_count_mismatch")
    cross = audit["cross_consistency"]
    if any(cross["row_field_mismatches"].values()) or cross["missing_expected_detail_count"] or cross["unexpected_detail_count"]:
        fatal.append("demand_not_explained_by_movement_filter")
    if cross["multiple_demand_units_product_count"]:
        fatal.append("unstable_demand_units")
    for key in ("snapshot_name_mismatch_product_count", "snapshot_unit_mismatch_product_count"):
        if cross[key]:
            warnings.append(key)
    if audit["sources"]["inventory"]["dates"]["future_to_snapshot_count"]:
        fatal.append("inventory_last_movement_after_snapshot")
    warnings.append("absence_not_observed_zero_source_completeness_unproven")
    audit.update(gate="FAILED" if fatal else "PASSED_WITH_WARNINGS" if warnings else "PASSED",
                 fatal_reasons=fatal, warnings=warnings)
    if fatal:
        raise DatasetAuditError(audit)


def _normalized(demand, movements, inventory, source_id):
    by_detail = {r["id_detalle"]: r for r in movements}
    grouped, traces = defaultdict(Decimal), defaultdict(list)
    identities = {}
    for row in demand:
        detail = by_detail[row["id_detalle"]]
        code, unit = row["codigo_producto"], detail["tipo_unidad"]
        key = row["fecha"], code, row["almacen"], unit
        grouped[key] += _decimal(row["cantidad"])
        traces[key].append(row["id_detalle"])
        identities[code] = {"product_code": code, "name": row["nombre_producto"], "unit": unit,
                            "warehouse_code": row["almacen"]}
    observations = [{**METADATA, "date": day, "product_code": code, "warehouse_code": warehouse,
                     "unit": unit, "quantity": float(quantity), "source_id": source_id,
                     "observation_status": "structured_positive", "operational_status": "unknown",
                     "observation_basis": "explicit_structured_demand_rows", "detail_ids": traces[(day, code, warehouse, unit)]}
                    for (day, code, warehouse, unit), quantity in sorted(grouped.items())]
    context = []
    for row in inventory:
        code = row["codigo_producto"]
        identity = identities.get(code)
        context.append({**METADATA, "product_code": code, "name": row["nombre_producto"], "unit": row["unidad"] or None,
                        "warehouse_code": row["id_almacen"] or None,
                        "stock": float(_decimal(row["stock_actual"])) if row["stock_actual"] else None,
                        "last_movement_date": _date(row["fecha_ultimo_movimiento"], local=True).isoformat() if row["fecha_ultimo_movimiento"] else None,
                        "snapshot_date": "2025-12-31", "snapshot_date_basis": "dataset_year_end_context_assumption",
                        "kardex_status": row["estado_kardex"],
                        "usable_for_demand_unit": bool(identity and identity["unit"] == row["unidad"] and row["stock_actual"]),
                        "demand_identity": identity})
    return observations, context, identities


def _fill_synthetic_calendar(observations):
    groups = defaultdict(dict)
    for row in observations:
        groups[(row["product_code"], row["warehouse_code"], row["unit"])][row["date"]] = row
    filled = []
    for group in groups.values():
        template = next(iter(group.values()))
        day = _date(min(group))
        while day <= date(2025, 12, 31):
            original = group.get(day.isoformat())
            filled.append({**(original or template), "date": day.isoformat(),
                           "quantity": original["quantity"] if original else 0.0,
                           "detail_ids": original["detail_ids"] if original else [],
                           "observation_status": "synthetic_scenario",
                           "observation_basis": "calendar_zero_fill_synthetic_scenario",
                           "operational_status": "scenario_calendar_day"})
            day += timedelta(days=1)
    return sorted(filled, key=lambda r: (r["date"], r["product_code"], r["warehouse_code"]))


def load_2025_bundle(demand_path, movements_path, inventory_path, *, calendar_zero_fill_synthetic_scenario=False):
    """Audit before constructing quantities; raises on a serious inconsistency.

    Default absent dates are unobserved, never zero. Explicit calendar completion
    is synthetic experimentation from each SKU's first positive date; it does
    not certify operational opening dates or source completeness.
    """
    rows, provenance = {}, {}
    for role, path in (("demand", demand_path), ("movements", movements_path), ("inventory", inventory_path)):
        rows[role], provenance[role] = _read(path, role)
    audit = {**METADATA, "sources": {role: _audit_source(rows[role], provenance[role], role) for role in rows},
             "cross_consistency": _cross_audit(rows["demand"], rows["movements"], rows["inventory"])}
    _gate(audit)
    observations, context, identities = _normalized(rows["demand"], rows["movements"], rows["inventory"], provenance["demand"]["source_id"])
    if calendar_zero_fill_synthetic_scenario:
        observations = _fill_synthetic_calendar(observations)
    metadata = {**METADATA, "read_only": True, "production_integration": False,
                "quantity_unit": "source_product_unit_no_conversion", "monetary_evaluation": False,
                "calendar_scenario": "calendar_zero_fill_synthetic_scenario" if calendar_zero_fill_synthetic_scenario else "explicit_positive_dates_only",
                "absence_is_observed_zero": False, "completeness_demonstrated": False,
                "snapshot_not_demand": True, "sources": provenance}
    return {"metadata": metadata, "audit": audit, "observations": observations,
            "inventory_context": context, "product_identity_by_code": identities,
            "activity_dates": sorted({r["fecha"] for r in rows["demand"]})}
