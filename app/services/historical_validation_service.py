"""Helpers puros de validación/normalización para el CSV histórico v1."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

CSV_HEADERS = (
    "event_date",
    "product_code",
    "product_name",
    "quantity",
    "record_type",
    "record_status",
    "document_number",
    "source_record_id",
    "source_line_id",
    "unit_price",
)

REQUIRED_MAPPING_FIELDS = (
    "event_date",
    "product_code",
    "quantity",
    "record_type",
    "record_status",
)

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_MULTIPART_BYTES = 12 * 1024 * 1024
MAX_ROWS = 50_000
MAX_COLUMNS = 40
MAX_CELL_CHARS = 4096

RECORD_TYPES = ("sale", "return", "cancellation", "correction")
RECORD_STATUSES = ("issued", "active", "cancelled", "voided", "superseded")
ACTIVE_RECORD_STATUSES = ("issued", "active")

# Matriz intencionalmente explícita. Una cancelación/corrección anulada se
# representa como ``voided``; ``cancelled`` se reserva para venta/devolución.
ALLOWED_TYPE_STATUS = {
    "sale": frozenset(RECORD_STATUSES),
    "return": frozenset(RECORD_STATUSES),
    "cancellation": frozenset(("issued", "active", "voided", "superseded")),
    "correction": frozenset(("issued", "active", "voided", "superseded")),
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$", re.ASCII)
_DECIMAL_RE = re.compile(r"^\d+(?:\.\d{1,2})?$", re.ASCII)
_FORMULA_PREFIXES = ("=", "+", "-", "@")
_MAX_DECIMAL_12_2 = Decimal("9999999999.99")


@dataclass(frozen=True)
class RowIssue:
    field_name: str | None
    error_code: str
    severity: str
    message: str


@dataclass(frozen=True)
class ParsedHistoricalRow:
    values: dict[str, Any]
    raw_row: dict[str, str]


class HeaderValidationError(ValueError):
    """Error seguro de header/mapping, apto para mostrar por API."""


def normalize_nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def strip_external(value: str) -> str:
    return normalize_nfc(value).strip()


def normalize_code(value: str) -> str:
    """NFC + trim externo + uppercase; no altera caracteres internos."""
    return strip_external(value).upper()


def normalize_identifier(value: str) -> str:
    """Normalización estable para documentos/IDs/fingerprint."""
    return strip_external(value).upper()


def normalize_source_identifier(value: str) -> str:
    """NFC + trim externo para IDs cuya capitalización es significativa."""
    return strip_external(value)


def normalize_name(value: str) -> str:
    """Normalización exclusiva para sugerencias, nunca para auto-match."""
    return " ".join(strip_external(value).casefold().split())


def has_dangerous_control(value: str) -> bool:
    """Detecta controles peligrosos en el stream, permitiendo delimitadores CSV."""
    for char in value:
        if char in ("\t", "\r", "\n"):
            continue
        if unicodedata.category(char) in {"Cc", "Cf", "Cs"}:
            return True
    return False


def has_dangerous_cell_control(value: str) -> bool:
    """En una celda/metadata ningún control o formato invisible es válido."""
    return any(
        unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value
    )


def starts_like_formula(value: str) -> bool:
    """Considera el primer carácter efectivo, ignorando espacio externo."""
    return bool(value) and value.lstrip().startswith(_FORMULA_PREFIXES)


def validate_metadata_identifier(
    value: str | None,
    *,
    field_name: str,
    max_length: int,
    default: str | None = None,
) -> str:
    candidate = strip_external(value or default or "")
    if not candidate:
        raise ValueError(f"El campo '{field_name}' es obligatorio.")
    if len(candidate) > max_length:
        raise ValueError(
            f"El campo '{field_name}' excede el máximo de {max_length} caracteres."
        )
    if has_dangerous_cell_control(candidate) or starts_like_formula(candidate):
        raise ValueError(f"El campo '{field_name}' contiene caracteres no permitidos.")
    normalized = normalize_identifier(candidate)
    if len(normalized) > max_length:
        raise ValueError(
            f"El campo '{field_name}' excede el máximo tras normalizarse."
        )
    return normalized


def validate_headers(headers: list[str]) -> list[str]:
    if not headers:
        raise HeaderValidationError("El CSV no contiene encabezados.")
    if len(headers) > MAX_COLUMNS:
        raise HeaderValidationError(
            f"El CSV excede el máximo de {MAX_COLUMNS} columnas."
        )

    clean: list[str] = []
    seen: set[str] = set()
    for header in headers:
        if len(header) > MAX_CELL_CHARS:
            raise HeaderValidationError(
                "Un encabezado excede el máximo permitido de caracteres."
            )
        normalized = strip_external(header)
        if not normalized:
            raise HeaderValidationError("El CSV contiene un encabezado vacío.")
        if has_dangerous_cell_control(normalized) or starts_like_formula(normalized):
            raise HeaderValidationError(
                "El CSV contiene un encabezado con caracteres no permitidos."
            )
        key = normalized.casefold()
        if key in seen:
            raise HeaderValidationError(
                "El CSV contiene encabezados duplicados o ambiguos."
            )
        seen.add(key)
        clean.append(normalized)
    return clean


def resolve_column_mapping(
    headers: list[str], requested_mapping: dict[str, str] | None
) -> dict[str, str]:
    """Resuelve mapping explícito o exige la plantilla exacta para auto-map."""
    clean_headers = validate_headers(headers)

    if requested_mapping is None:
        if tuple(clean_headers) != CSV_HEADERS:
            raise HeaderValidationError(
                "Los encabezados no coinciden exactamente con la plantilla v1; "
                "envíe un mapping explícito."
            )
        return {name: name for name in CSV_HEADERS}

    if not isinstance(requested_mapping, dict):
        raise HeaderValidationError("El campo 'mapping' debe ser un objeto JSON.")

    unknown = set(requested_mapping) - set(CSV_HEADERS)
    if unknown:
        raise HeaderValidationError(
            "El mapping contiene nombres canónicos no permitidos."
        )

    header_set = set(clean_headers)
    result: dict[str, str] = {}
    used_sources: set[str] = set()
    for canonical, source in requested_mapping.items():
        if not isinstance(source, str):
            raise HeaderValidationError(
                "Cada valor del mapping debe ser el nombre exacto de un encabezado."
            )
        source_clean = strip_external(source)
        if source_clean not in header_set:
            raise HeaderValidationError(
                f"El encabezado mapeado para '{canonical}' no existe en el CSV."
            )
        if source_clean in used_sources:
            raise HeaderValidationError(
                "Una columna de origen no puede mapearse a más de un campo."
            )
        result[canonical] = source_clean
        used_sources.add(source_clean)

    missing = [name for name in REQUIRED_MAPPING_FIELDS if name not in result]
    if missing:
        raise HeaderValidationError(
            "El mapping no incluye todos los campos obligatorios: "
            + ", ".join(missing)
            + "."
        )
    return result


def canonicalize_csv_row(
    headers: list[str],
    row: list[str],
    mapping: dict[str, str],
) -> dict[str, str]:
    source_values = dict(zip(headers, row))
    return {
        canonical: source_values.get(mapping.get(canonical, ""), "")
        for canonical in CSV_HEADERS
    }


def parse_date_2025(value: str) -> date:
    candidate = strip_external(value)
    if not _DATE_RE.fullmatch(candidate):
        raise ValueError("La fecha debe tener formato exacto YYYY-MM-DD, sin hora.")
    try:
        parsed = date.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError("La fecha no es válida.") from exc
    if parsed.year != 2025:
        raise ValueError("La fecha debe pertenecer al año 2025.")
    return parsed


def parse_decimal_12_2(
    value: str,
    *,
    field_name: str,
    required: bool,
    strictly_positive: bool,
) -> Decimal | None:
    candidate = strip_external(value)
    if not candidate:
        if required:
            raise ValueError(f"El campo '{field_name}' es obligatorio.")
        return None
    lowered = candidate.casefold()
    if "e" in lowered:
        raise ValueError(
            f"El campo '{field_name}' no acepta notación científica."
        )
    if lowered in {"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity"}:
        raise ValueError(f"El campo '{field_name}' debe ser un decimal finito.")
    if candidate.startswith("-"):
        raise ValueError(f"El campo '{field_name}' no acepta valores negativos.")
    if not _DECIMAL_RE.fullmatch(candidate):
        raise ValueError(
            f"El campo '{field_name}' debe ser decimal con máximo 2 decimales."
        )
    try:
        parsed = Decimal(candidate)
    except InvalidOperation as exc:
        raise ValueError(f"El campo '{field_name}' no es un decimal válido.") from exc
    if not parsed.is_finite():
        raise ValueError(f"El campo '{field_name}' debe ser un decimal finito.")
    if parsed > _MAX_DECIMAL_12_2:
        raise ValueError(f"El campo '{field_name}' excede DECIMAL(12,2).")
    if strictly_positive and parsed <= 0:
        raise ValueError(f"El campo '{field_name}' debe ser mayor que 0.")
    if not strictly_positive and parsed < 0:
        raise ValueError(f"El campo '{field_name}' no acepta valores negativos.")
    return parsed.quantize(Decimal("0.01"))


def decimal_fingerprint_value(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def _text_field(
    raw: str,
    *,
    field_name: str,
    required: bool,
    max_length: int,
    formula_safe: bool = True,
    preserve_original: bool = False,
) -> str | None:
    if len(raw) > MAX_CELL_CHARS:
        raise ValueError(
            f"El campo '{field_name}' excede {MAX_CELL_CHARS} caracteres."
        )
    value = strip_external(raw)
    if not value:
        if required:
            raise ValueError(f"El campo '{field_name}' es obligatorio.")
        return None
    if len(value) > max_length or (preserve_original and len(raw) > max_length):
        raise ValueError(
            f"El campo '{field_name}' excede el máximo de {max_length} caracteres."
        )
    if has_dangerous_cell_control(value):
        raise ValueError(f"El campo '{field_name}' contiene controles no permitidos.")
    if formula_safe and starts_like_formula(value):
        raise ValueError(
            f"El campo '{field_name}' comienza con un prefijo de fórmula peligroso."
        )
    # Los campos ``original_*`` deben conservar exactamente el texto recibido.
    # La versión NFC/trim se calcula de forma separada para comparar y crear
    # fingerprints; nunca se sobrescribe silenciosamente el dato de origen.
    return raw if preserve_original else value


def validate_historical_row(raw_row: dict[str, str]) -> tuple[
    ParsedHistoricalRow | None, list[RowIssue]
]:
    """Valida una fila completa sin tocar base de datos."""
    issues: list[RowIssue] = []

    for key in CSV_HEADERS:
        raw_value = raw_row.get(key, "")
        if len(raw_value) > MAX_CELL_CHARS:
            issues.append(
                RowIssue(
                    key,
                    "cell_too_long",
                    "error",
                    f"El campo '{key}' excede {MAX_CELL_CHARS} caracteres.",
                )
            )
        elif has_dangerous_cell_control(raw_value):
            issues.append(
                RowIssue(
                    key,
                    "dangerous_control_character",
                    "error",
                    f"El campo '{key}' contiene controles Unicode no permitidos.",
                )
            )
    if issues:
        return None, issues

    def capture(field: str, code: str, callback):
        try:
            return callback()
        except ValueError as exc:
            issues.append(RowIssue(field, code, "error", str(exc)))
            return None

    event_date = capture(
        "event_date", "invalid_event_date", lambda: parse_date_2025(raw_row["event_date"])
    )
    product_code_original = capture(
        "product_code",
        "invalid_product_code",
        lambda: _text_field(
            raw_row["product_code"],
            field_name="product_code",
            required=True,
            max_length=255,
            preserve_original=True,
        ),
    )
    product_name_original = capture(
        "product_name",
        "invalid_product_name",
        lambda: _text_field(
            raw_row["product_name"],
            field_name="product_name",
            required=False,
            max_length=255,
            preserve_original=True,
        ),
    )
    quantity = capture(
        "quantity",
        "invalid_quantity",
        lambda: parse_decimal_12_2(
            raw_row["quantity"],
            field_name="quantity",
            required=True,
            strictly_positive=True,
        ),
    )

    record_type = capture(
        "record_type",
        "invalid_record_type",
        lambda: _text_field(
            raw_row["record_type"],
            field_name="record_type",
            required=True,
            max_length=20,
        ),
    )
    if record_type is not None:
        record_type = record_type.casefold()
        if record_type not in RECORD_TYPES:
            issues.append(
                RowIssue(
                    "record_type",
                    "invalid_record_type",
                    "error",
                    "record_type debe ser sale, return, cancellation o correction.",
                )
            )

    record_status = capture(
        "record_status",
        "invalid_record_status",
        lambda: _text_field(
            raw_row["record_status"],
            field_name="record_status",
            required=True,
            max_length=20,
        ),
    )
    if record_status is not None:
        record_status = record_status.casefold()
        if record_status not in RECORD_STATUSES:
            issues.append(
                RowIssue(
                    "record_status",
                    "invalid_record_status",
                    "error",
                    "record_status debe ser issued, active, cancelled, voided o superseded.",
                )
            )

    document_original = capture(
        "document_number",
        "invalid_document_number",
        lambda: _text_field(
            raw_row["document_number"],
            field_name="document_number",
            required=False,
            max_length=255,
            preserve_original=True,
        ),
    )
    source_record_original = capture(
        "source_record_id",
        "invalid_source_record_id",
        lambda: _text_field(
            raw_row["source_record_id"],
            field_name="source_record_id",
            required=False,
            max_length=255,
            preserve_original=True,
        ),
    )
    source_line_original = capture(
        "source_line_id",
        "invalid_source_line_id",
        lambda: _text_field(
            raw_row["source_line_id"],
            field_name="source_line_id",
            required=False,
            max_length=255,
            preserve_original=True,
        ),
    )
    unit_price = capture(
        "unit_price",
        "invalid_unit_price",
        lambda: parse_decimal_12_2(
            raw_row["unit_price"],
            field_name="unit_price",
            required=False,
            strictly_positive=False,
        ),
    )

    if (
        record_type in ALLOWED_TYPE_STATUS
        and record_status in RECORD_STATUSES
        and record_status not in ALLOWED_TYPE_STATUS[record_type]
    ):
        issues.append(
            RowIssue(
                "record_status",
                "invalid_type_status_combination",
                "error",
                "La combinación record_type/record_status no está permitida.",
            )
        )

    if record_type in {"return", "cancellation", "correction"} and not document_original:
        issues.append(
            RowIssue(
                "document_number",
                "related_document_required",
                "error",
                "Este tipo de registro requiere document_number para vincularse.",
            )
        )

    product_code_normalized = (
        normalize_code(product_code_original) if product_code_original else None
    )
    product_name_normalized = (
        normalize_name(product_name_original) if product_name_original else None
    )
    document_normalized = (
        normalize_identifier(document_original) if document_original else None
    )
    source_record_normalized = (
        normalize_source_identifier(source_record_original)
        if source_record_original
        else None
    )
    source_line_normalized = (
        normalize_source_identifier(source_line_original) if source_line_original else None
    )
    normalized_lengths = (
        ("product_code", product_code_normalized),
        ("product_name", product_name_normalized),
        ("document_number", document_normalized),
        ("source_record_id", source_record_normalized),
        ("source_line_id", source_line_normalized),
    )
    for field_name, normalized_value in normalized_lengths:
        if normalized_value is not None and len(normalized_value) > 255:
            issues.append(
                RowIssue(
                    field_name,
                    "normalized_value_too_long",
                    "error",
                    f"El campo '{field_name}' excede 255 caracteres tras normalizarse.",
                )
            )

    if issues:
        return None, issues

    assert event_date is not None
    assert product_code_original is not None
    assert quantity is not None
    assert record_type is not None
    assert record_status is not None

    values = {
        "event_date": event_date,
        "product_code_original": product_code_original,
        "product_code_normalized": product_code_normalized,
        "product_name_original": product_name_original,
        "product_name_normalized": product_name_normalized,
        "quantity": quantity,
        "unit_price": unit_price,
        "record_type": record_type,
        "record_status": record_status,
        "effective_status": record_status,
        "document_number_original": document_original,
        "document_number_normalized": document_normalized,
        "source_record_id_original": source_record_original,
        "source_record_id_normalized": source_record_normalized,
        "source_line_id_original": source_line_original,
        "source_line_id_normalized": source_line_normalized,
    }
    # Copia nueva y allowlist: nunca persiste columnas CSV desconocidas.
    safe_raw = {name: raw_row.get(name, "") for name in CSV_HEADERS}
    return ParsedHistoricalRow(values=values, raw_row=safe_raw), []
