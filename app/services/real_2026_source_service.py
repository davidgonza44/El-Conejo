"""Validate the isolated real-2026 package without application or database access."""
from __future__ import annotations

import csv
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import re


BUILD_SHA256 = "087bda535bc60f49f773d026434e10df5adcf5ce59fbc8669ffc861dd9ce82f8"
REPORT_FILE = "empirical_2026_build_report.json"
MANIFEST_FILE = "empirical_2026_cohort_manifest.csv"
CALENDAR_FILE = "empirical_2026_operational_calendar.csv"
SERIES_FILES = {
    "STRICT": "empirical_2026_series_strict.csv",
    "SENSITIVITY": "empirical_2026_series_sensitivity.csv",
}
CATALOG_FILE = "productos_2026.csv"
RECEIPT_FILE = "MANIFIESTO_SHA256.json"
REQUIRED_FILES = (REPORT_FILE, MANIFEST_FILE, CALENDAR_FILE,
                  *SERIES_FILES.values(), CATALOG_FILE)
ANCHOR_FILES = (CATALOG_FILE, MANIFEST_FILE, CALENDAR_FILE)
OPTIONAL_FILES = (
    "movimientos_detalle_2026.csv",
    "movimientos_detalle_2026_auditoria_18092026.csv",
    "demanda_limpia_2026.csv", "existencias_2026_final.csv",
    "auditoria_elegibilidad_semantica_2026.md",
)
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_DAILY_POINTS = 400_000
FILE_LIMITS = {
    REPORT_FILE: 64 * 1024,
    MANIFEST_FILE: 1024 * 1024,
    CALENDAR_FILE: 128 * 1024,
    SERIES_FILES["STRICT"]: 32 * 1024 * 1024,
    SERIES_FILES["SENSITIVITY"]: 2 * 1024 * 1024,
    CATALOG_FILE: 4 * 1024 * 1024,
}
MANIFEST_FIELDS = (
    "codigo_producto", "nombre_actual", "cohorte", "estado_identidad",
    "estado_unidad", "is_service", "incluido_pronostico",
    "incluido_reabastecimiento", "motivo",
)
SERIES_FIELDS = (
    "codigo_producto", "nombre_actual", "cohorte", "is_service", "fecha_real",
    "operational_index", "product_operational_index", "demanda_observada",
    "es_cero_observado",
)
CALENDAR_FIELDS = ("fecha_real", "operational_index", "tipo_dia", "evidencia")
COHORTS = ("STRICT", "SENSITIVITY", "QUARANTINE")
IDENTITY_STATUSES = {
    "CONTINUIDAD_PLAUSIBLE", "CAMBIO_CLARO_DE_ARTICULO", "REVISAR_IDENTIDAD",
}
UNIT_STATUSES = {
    "UNIDAD_SIMPLE", "VARIANTE_TEXTUAL_PLAUSIBLE",
    "MULTIUNIDAD_NO_RESUELTA", "MULTICANT_ESPECIAL",
}
START, END = date(2026, 1, 5), date(2026, 9, 18)
_CLOSURES = set(map(date.fromisoformat, (
    "2026-01-10", "2026-02-16", "2026-02-17", "2026-04-02",
    "2026-04-03", "2026-04-04", "2026-05-01",
)))
_OPENINGS = {date(2026, 5, 3), date(2026, 5, 17)}


def is_operational_day(day):
    """Regla confirmada por la empresa: lunes a sábado, con los cierres y aperturas documentados."""
    return (day.weekday() != 6 and day not in _CLOSURES) or day in _OPENINGS


def operational_days_after(cutoff, count):
    """Siguientes días operativos tras el corte; no supone feriados futuros sin documentar."""
    days, day = [], cutoff
    while len(days) < count:
        day += timedelta(days=1)
        if is_operational_day(day):
            days.append(day)
    return days


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _validate_files(files):
    _require(isinstance(files, dict), "El paquete de archivos no es válido.")
    _require(set(REQUIRED_FILES).issubset(files), "Faltan archivos obligatorios del paquete real 2026.")
    _require(set(files).issubset(set(REQUIRED_FILES) | set(OPTIONAL_FILES) | {RECEIPT_FILE}),
             "El paquete contiene un archivo no permitido.")
    _require(all(isinstance(value, bytes) and value for value in files.values()),
             "Los archivos deben contener datos binarios no vacíos.")
    _require(sum(map(len, files.values())) <= MAX_TOTAL_BYTES,
             "El paquete supera el límite de 64 MiB.")
    for name, data in files.items():
        _require(len(data) <= FILE_LIMITS.get(name, 32 * 1024 * 1024),
                 "Un archivo supera el tamaño permitido.")
    return {name: _digest(data) for name, data in files.items()}


def _validate_receipt(files, hashes):
    if RECEIPT_FILE not in files:
        return
    names = {CATALOG_FILE, "demanda_limpia_2026.csv", "movimientos_detalle_2026.csv", "existencias_2026_final.csv"}
    _require(names.issubset(files), "El manifiesto SHA-256 requiere los cuatro originales aportados.")
    try:
        entries = json.loads(files[RECEIPT_FILE].decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("El manifiesto SHA-256 no es un JSON válido.") from None
    _require(isinstance(entries, list) and len(entries) == len(names), "El manifiesto SHA-256 no es válido.")
    seen = set()
    for item in entries:
        _require(isinstance(item, dict), "El manifiesto SHA-256 contiene una entrada inválida.")
        name = item.get("archivo_del_zip")
        _require(isinstance(name, str) and name in names and name not in seen,
                 "El manifiesto SHA-256 contiene archivos repetidos o desconocidos.")
        _require(item.get("tamano_bytes") == len(files[name]) and item.get("sha256") == hashes[name],
                 "El manifiesto SHA-256 no coincide con los originales aportados.")
        seen.add(name)


def _hash_map(value):
    _require(isinstance(value, dict) and bool(value), "El informe no contiene hashes válidos.")
    _require(all(isinstance(key, str) and isinstance(item, str)
                 and re.fullmatch(r"[0-9a-f]{64}", item) for key, item in value.items()),
             "El informe contiene hashes inválidos.")
    return value


def _read_report(files, hashes, allow_revision):
    reference_match = hashes[REPORT_FILE] == BUILD_SHA256
    _require(reference_match or allow_revision,
             "El informe no corresponde al paquete real 2026 de referencia.")
    try:
        report = json.loads(files[REPORT_FILE].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("El informe JSON no es válido.") from None
    _require(isinstance(report, dict) and report.get("READY_FOR_BACKTEST") is True,
             "El informe no declara un paquete preparado y válido.")
    before = _hash_map(report.get("source_sha256_before"))
    _require(set(before).issubset({CATALOG_FILE, *OPTIONAL_FILES}),
             "El informe contiene una fuente original no permitida.")
    _require(before == _hash_map(report.get("source_sha256_after")),
             "Los hashes de las fuentes originales no coinciden.")
    artifacts = _hash_map(report.get("artifact_sha256"))
    for name in (MANIFEST_FILE, CALENDAR_FILE, *SERIES_FILES.values()):
        _require(hashes[name] == artifacts.get(name),
                 "Un archivo normalizado no coincide con el hash del informe.")
    for name in (CATALOG_FILE, *(name for name in OPTIONAL_FILES if name in files)):
        _require(hashes[name] == before.get(name),
                 "Una fuente original no coincide con el hash del informe.")
    return report, reference_match


def _csv_rows(data, fields, *, exact=True):
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
        header = reader.fieldnames or []
        valid = header == list(fields) if exact else set(fields).issubset(header)
        _require(valid and len(header) == len(set(header)), "Un CSV contiene columnas inválidas.")
        for row in reader:
            _require(None not in row and all(value is not None for value in row.values()),
                     "Un CSV contiene una fila incompleta o columnas adicionales.")
            yield row
    except (UnicodeError, csv.Error):
        raise ValueError("Un archivo CSV no es válido o no está codificado en UTF-8.") from None


def _text(value, maximum, label):
    _require(isinstance(value, str) and bool(value) and value == value.strip()
             and len(value) <= maximum and not any(ord(char) < 32 for char in value),
             f"El paquete contiene {label} inválido.")
    return value


def _boolean(value):
    _require(value in ("true", "false"), "El paquete contiene un indicador booleano inválido.")
    return value == "true"


def _integer(value):
    _require(isinstance(value, str) and re.fullmatch(r"[1-9]\d{0,8}", value),
             "El paquete contiene un índice inválido.")
    return int(value)


def _day(value):
    _require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value),
             "El paquete contiene una fecha inválida.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("El paquete contiene una fecha inválida.") from None


def _quantity(value):
    _require(isinstance(value, str) and re.fullmatch(r"\d{1,18}(?:\.\d{1,6})?", value),
             "El paquete contiene una cantidad inválida.")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError("El paquete contiene una cantidad inválida.") from None
    _require(amount.is_finite() and amount >= 0, "El paquete contiene una cantidad inválida.")
    return amount


def _calendar(data, report):
    days = []
    for row in _csv_rows(data, CALENDAR_FIELDS):
        _require(_integer(row["operational_index"]) == len(days) + 1,
                 "Los índices del calendario no son consecutivos.")
        days.append(_day(row["fecha_real"]))
    expected = [START + timedelta(days=index) for index in range((END - START).days + 1)]
    expected = [day for day in expected if is_operational_day(day)]
    _require(days == expected and len(days) == 216,
             "El calendario no coincide con los 216 días operativos confirmados de 2026.")
    protocol = report.get("protocol")
    _require(isinstance(protocol, dict) and protocol.get("start") == START.isoformat()
             and protocol.get("cutoff") == END.isoformat(),
             "La cobertura declarada no coincide con el calendario real 2026.")
    return days


def _catalog(data):
    catalog = {}
    fields = ("codigo", "nombre", "unidad_deventa_a")
    for row in _csv_rows(data, fields, exact=False):
        code = _text(row["codigo"], 50, "un código")
        _require(code not in catalog, "El catálogo contiene códigos de producto repetidos.")
        catalog[code] = (row["nombre"], row["unidad_deventa_a"])
    return catalog


def _product(row, catalog):
    code = _text(row["codigo_producto"], 50, "un código")
    name = _text(row["nombre_actual"], 500, "un nombre")
    cohort, identity, unit_status = row["cohorte"], row["estado_identidad"], row["estado_unidad"]
    _require(cohort in COHORTS and identity in IDENTITY_STATUSES and unit_status in UNIT_STATUSES,
             "El manifiesto contiene una clasificación desconocida.")
    _require(code in catalog and catalog[code][0] == name,
             "El catálogo y el manifiesto no identifican el mismo producto.")
    unit = _text(catalog[code][1], 100, "una unidad")
    is_service = _boolean(row["is_service"])
    included = _boolean(row["incluido_pronostico"])
    _require(included == (cohort != "QUARANTINE")
             and _boolean(row["incluido_reabastecimiento"]) == (included and not is_service),
             "Los indicadores de inclusión del manifiesto no son coherentes.")
    expected_unit = {"STRICT": "UNIDAD_SIMPLE", "SENSITIVITY": "VARIANTE_TEXTUAL_PLAUSIBLE"}
    if included:
        _require(identity == "CONTINUIDAD_PLAUSIBLE" and unit_status == expected_unit[cohort],
                 "La identidad o unidad no corresponde a la cohorte declarada.")
    reason = _text(row["motivo"], 2000, "un motivo")
    return {"code": code, "name": name, "unit": unit, "cohort": cohort,
            "identity_status": identity, "unit_status": unit_status,
            "is_service": is_service, "included": included, "reason": reason, "points": []}


def _products(data, catalog, report):
    products = {}
    for row in _csv_rows(data, MANIFEST_FIELDS):
        product = _product(row, catalog)
        _require(product["code"] not in products, "El manifiesto contiene códigos repetidos.")
        products[product["code"]] = product
    counts = {cohort: sum(item["cohort"] == cohort for item in products.values()) for cohort in COHORTS}
    _require(bool(products) and report.get("cohort_counts") == counts,
             "Los productos del manifiesto no coinciden con los recuentos del informe.")
    return products, counts


def _append_point(row, products, cohort, positions):
    code = row["codigo_producto"]
    product = products.get(code)
    _require(product is not None and product["cohort"] == cohort and row["cohorte"] == cohort,
             "Una serie contiene un producto ajeno a su cohorte.")
    _require(row["nombre_actual"] == product["name"]
             and _boolean(row["is_service"]) == product["is_service"],
             "Los datos del producto no coinciden entre la serie y el manifiesto.")
    day, amount = _day(row["fecha_real"]), _quantity(row["demanda_observada"])
    global_index = positions.get(day)
    _require(global_index is not None and _integer(row["operational_index"]) == global_index + 1,
             "Una observación no coincide con el calendario operativo.")
    points = product["points"]
    _require(_integer(row["product_operational_index"]) == len(points) + 1,
             "La serie contiene índices de producto repetidos o discontinuos.")
    _require(_boolean(row["es_cero_observado"]) == (amount == 0),
             "El indicador de cero observado no coincide con la cantidad.")
    if points:
        _require(global_index == positions[points[-1][0]] + 1,
                 "La serie contiene fechas duplicadas, desordenadas o huecos.")
    else:
        _require(amount > 0, "Una serie no puede comenzar con ceros anteriores a su primera demanda.")
    points.append((day, amount))


def _series(files, products, calendar):
    positions = {day: index for index, day in enumerate(calendar)}
    total = 0
    for cohort, filename in SERIES_FILES.items():
        for row in _csv_rows(files[filename], SERIES_FIELDS):
            total += 1
            _require(total <= MAX_DAILY_POINTS, "El paquete supera el límite de observaciones diarias.")
            _append_point(row, products, cohort, positions)
    for product in products.values():
        points = product["points"]
        if product["included"]:
            _require(bool(points) and points[-1][0] == calendar[-1],
                     "Una serie incluida no cubre su período completo hasta el corte.")
    return total


def _summary(products, counts, total, calendar, reference_match):
    positive = sum(amount > 0 for product in products.values() for _, amount in product["points"])
    return {"source": "real_observed_2026", "products": len(products), "total_points": total,
            "cohort_counts": counts, "positive_points": positive, "zero_points": total - positive,
            "calendar_days": len(calendar), "services": sum(item["is_service"] for item in products.values()),
            "coverage_start": calendar[0].isoformat(), "coverage_end": calendar[-1].isoformat(),
            "reference_match": reference_match}


def _provenance(files, report, reference_match):
    originals = report["source_sha256_before"]
    missing = sorted(name for name in originals if name not in files)
    note = ""
    if "movimientos_detalle_2026_auditoria_18092026.csv" in missing:
        note = ("Series canónicas verificadas; reconstrucción íntegra desde movimientos "
                "requiere el archivo adicional de auditoría.")
    return {"reference_match": reference_match, "reported_original_sha256": originals,
            "provided_originals": [name for name in (CATALOG_FILE, *OPTIONAL_FILES) if name in files],
            "missing_originals": missing, "parser_version": "real2026-v1",
            "observation_basis": ("verified_frozen_operational_suffix" if reference_match
                                  else "validated_revision_operational_suffix"),
            "receipt_manifest_verified": RECEIPT_FILE in files,
            "raw_traceability_note": note}


def parse_package(files: dict[str, bytes], *, allow_revision=False) -> dict:
    """Validate a complete snapshot; parent-version authorization belongs to the import service.

    Optional original files are verified and recorded, never parsed or combined
    with the already canonical daily observations. No dates or zeros are added.
    """
    hashes = _validate_files(files)
    _validate_receipt(files, hashes)
    report, reference_match = _read_report(files, hashes, allow_revision)
    calendar = _calendar(files[CALENDAR_FILE], report)
    catalog = _catalog(files[CATALOG_FILE])
    products, counts = _products(files[MANIFEST_FILE], catalog, report)
    total = _series(files, products, calendar)
    required_hashes = {name: hashes[name] for name in REQUIRED_FILES}
    package_hash = _digest(json.dumps(required_hashes, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return {"package_hash": package_hash, "source_hashes": hashes,
            "provenance": _provenance(files, report, reference_match),
            "coverage_start": calendar[0], "coverage_end": calendar[-1], "calendar": calendar,
            "products": sorted(products.values(), key=lambda item: item["code"]),
            "summary": _summary(products, counts, total, calendar, reference_match)}
