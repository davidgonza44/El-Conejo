"""Orquestación segura del módulo de importación histórica CSV v1.

Este servicio solo escribe las tres tablas históricas y archivos privados.
Nunca crea productos, movimientos, notas ni modifica stock.
"""
from __future__ import annotations

import codecs
import csv
import hashlib
import hmac
import io
import re
import secrets
import threading
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from flask import current_app
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models import (
    HistoricalDemandRecord,
    HistoricalImport,
    HistoricalImportError,
    Product,
)
from app.models.historical_demand_record import (
    RECORD_STATUS_ACTIVE,
    RECORD_STATUS_ISSUED,
    RECORD_STATUS_SUPERSEDED,
    RECORD_TYPE_CANCELLATION,
    RECORD_TYPE_CORRECTION,
    RECORD_TYPE_RETURN,
    RECORD_TYPE_SALE,
)
from app.models.historical_import import (
    HISTORICAL_IMPORT_STATUSES,
    IMPORT_STATUS_CONFIRMED,
    IMPORT_STATUS_DRY_RUN_READY,
    IMPORT_STATUS_PREVIEWED,
    IMPORT_STATUS_REVERTED,
    IMPORT_STATUS_UPLOADED,
)
from app.models.historical_import_error import (
    RESOLUTION_NOT_REQUIRED,
    RESOLUTION_RESOLVED,
    RESOLUTION_UNRESOLVED,
    SEVERITY_ERROR,
    SEVERITY_REVIEW,
    SEVERITY_WARNING,
)
from app.services.exceptions import ApiError, ConflictError, NotFoundError, ValidationError
from app.services.historical_deduplication_service import (
    FINGERPRINT_VERSION,
    build_fingerprint,
    sha256_file,
)
from app.services.historical_matching_service import (
    build_product_indexes,
    match_product,
)
from app.services.historical_validation_service import (
    ACTIVE_RECORD_STATUSES,
    CSV_HEADERS,
    MAX_CELL_CHARS,
    MAX_COLUMNS,
    MAX_FILE_BYTES,
    MAX_ROWS,
    REQUIRED_MAPPING_FIELDS,
    HeaderValidationError,
    canonicalize_csv_row,
    has_dangerous_cell_control,
    has_dangerous_control,
    normalize_code,
    normalize_identifier,
    normalize_name,
    resolve_column_mapping,
    starts_like_formula,
    strip_external,
    validate_headers,
    validate_historical_row,
    validate_metadata_identifier,
)

PRIVATE_STORAGE_DIR = "historical_imports"
CSV_ENCODING = "utf-8-sig"
CSV_DELIMITER = ";"
CSV_BOM = codecs.BOM_UTF8
COPY_CHUNK_BYTES = 64 * 1024
CONFIRMATION_TOKEN_TTL_MINUTES = 15
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

_MATCH_STATUSES = frozenset(
    {
        "pending",
        "code_collision",
        "inactive_review",
        "exact",
        "name_suggested",
        "unmatched",
        "manual_inactive_approved",
        "manual_confirmed",
    }
)
_DUPLICATE_ERROR_CODES = frozenset(
    {
        "strong_duplicate_in_file",
        "strong_duplicate_existing",
        "weak_possible_duplicate",
    }
)
_PRODUCT_MATCH_ISSUE_CODES = frozenset(
    {
        "product_code_collision",
        "product_unmatched",
        "product_name_suggestion",
        "product_inactive",
    }
)

_STORAGE_KEY_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.csv$"
)
_CSV_LIMIT_LOCK = threading.Lock()

_DYNAMIC_ERROR_CODES = frozenset(
    {
        "product_code_collision_critical",
        "product_code_collision",
        "product_unmatched",
        "product_name_suggestion",
        "product_inactive",
        "product_name_mismatch",
        "manual_product_match",
        "strong_duplicate_in_file",
        "strong_duplicate_existing",
        "weak_possible_duplicate",
        "related_record_missing",
        "related_record_ambiguous",
        "related_record_invalid",
        "manual_relationship",
        "relationship_cycle",
        "correction_target_repeated",
        "related_quantity_exceeded",
        "negative_net_demand",
    }
)

_REVIEW_FLAGS = frozenset(
    {
        "weak_duplicate",
        "inactive_product",
        "negative_net",
        "manual_match",
        "relationship",
    }
)


def _api_error(message: str, status_code: int) -> ApiError:
    return ApiError(message, status_code=status_code)


def _safe_original_filename(filename: str | None) -> str:
    if not filename:
        raise ValidationError("Debe adjuntar un archivo CSV.")
    # Los navegadores pueden enviar una ruta cliente; solo se conserva basename.
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    basename = strip_external(basename)
    if not basename or len(basename) > 255:
        raise ValidationError("El nombre original del archivo no es válido.")
    if has_dangerous_cell_control(basename) or starts_like_formula(basename):
        raise ValidationError("El nombre original del archivo contiene caracteres no permitidos.")
    if Path(basename).suffix.casefold() != ".csv":
        raise ValidationError("Solo se aceptan archivos con extensión .csv.")
    return basename


def _private_storage_root() -> Path:
    root = Path(current_app.instance_path) / PRIVATE_STORAGE_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _storage_path(storage_key: str) -> Path:
    if not _STORAGE_KEY_RE.fullmatch(storage_key):
        raise ConflictError("La referencia privada del archivo no es válida.")
    root = _private_storage_root()
    path = (root / storage_key).resolve()
    if path.parent != root:
        raise ConflictError("La referencia privada del archivo no es válida.")
    return path


def _copy_upload_to_private(stream) -> tuple[str, Path, str, int]:
    storage_key = f"{uuid4()}.csv"
    destination = _storage_path(storage_key)
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8-sig")(errors="strict")
    total = 0
    first_chunk = True

    try:
        with destination.open("xb") as target:
            while True:
                chunk = stream.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                if first_chunk:
                    first_chunk = False
                    if not chunk.startswith(CSV_BOM):
                        raise _api_error(
                            "El CSV debe estar codificado como UTF-8-sig e incluir BOM.",
                            422,
                        )
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise _api_error(
                        "El archivo excede el máximo permitido de 10 MiB.", 413
                    )
                digest.update(chunk)
                target.write(chunk)
                try:
                    decoded = decoder.decode(chunk, final=False)
                except UnicodeDecodeError as exc:
                    raise _api_error(
                        "El archivo no es un CSV UTF-8-sig válido.", 422
                    ) from exc
                if has_dangerous_control(decoded):
                    raise _api_error(
                        "El archivo contiene controles Unicode no permitidos.", 422
                    )

            if first_chunk:
                raise _api_error("El archivo CSV está vacío.", 422)
            try:
                tail = decoder.decode(b"", final=True)
            except UnicodeDecodeError as exc:
                raise _api_error(
                    "El archivo no es un CSV UTF-8-sig válido.", 422
                ) from exc
            if has_dangerous_control(tail):
                raise _api_error(
                    "El archivo contiene controles Unicode no permitidos.", 422
                )
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    return storage_key, destination, digest.hexdigest(), total


def _inspect_csv_structure(path: Path) -> dict[str, object]:
    """Escaneo streaming de límites estructurales, sin validar negocio."""
    with _CSV_LIMIT_LOCK:
        previous_limit = csv.field_size_limit()
        csv.field_size_limit(MAX_CELL_CHARS)
        try:
            with path.open("r", encoding=CSV_ENCODING, newline="") as source:
                reader = csv.reader(
                    source,
                    delimiter=CSV_DELIMITER,
                    quotechar='"',
                    strict=True,
                )
                try:
                    headers = next(reader)
                except StopIteration as exc:
                    raise _api_error("El CSV no contiene encabezados.", 422) from exc
                clean_headers = validate_headers(headers)
                if len(clean_headers) < len(REQUIRED_MAPPING_FIELDS):
                    raise HeaderValidationError(
                        "El CSV no usa el delimitador punto y coma o no contiene "
                        "las columnas mínimas requeridas."
                    )

                row_count = 0
                for row in reader:
                    row_count += 1
                    if row_count > MAX_ROWS:
                        raise _api_error(
                            f"El CSV excede el máximo de {MAX_ROWS} filas.", 413
                        )
                    if not row:
                        continue
                    if len(row) != len(clean_headers):
                        raise _api_error(
                            "Una fila no tiene la misma cantidad de columnas que "
                            "el encabezado; verifique el delimitador punto y coma.",
                            422,
                        )
                    if len(row) > MAX_COLUMNS:
                        raise _api_error(
                            f"Una fila excede el máximo de {MAX_COLUMNS} columnas.",
                            422,
                        )
                    if any(len(cell) > MAX_CELL_CHARS for cell in row):
                        raise _api_error(
                            f"Una celda excede el máximo de {MAX_CELL_CHARS} caracteres.",
                            422,
                        )
                # Se persisten exclusivamente los encabezados ya validados. Esto
                # permite reconstruir el mapping explícito tras recargar la UI
                # sin volver a exponer ni leer el archivo privado desde el cliente.
                return {
                    "structural_rows": row_count,
                    "column_count": len(clean_headers),
                    "headers": clean_headers,
                }
        except csv.Error as exc:
            raise _api_error(
                "El CSV está mal formado o una celda excede el límite permitido.",
                422,
            ) from exc
        except HeaderValidationError as exc:
            raise _api_error(str(exc), 422) from exc
        finally:
            csv.field_size_limit(previous_limit)


def upload_import(
    file_storage,
    *,
    source_system: str | None,
    document_type: str | None,
    actor_user_id: int,
) -> HistoricalImport:
    original_filename = _safe_original_filename(
        getattr(file_storage, "filename", None)
    )
    try:
        normalized_source = validate_metadata_identifier(
            source_system,
            field_name="source_system",
            max_length=100,
        )
        normalized_document_type = validate_metadata_identifier(
            document_type,
            field_name="document_type",
            max_length=50,
            default="historical_demand",
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    storage_key, path, file_hash, size = _copy_upload_to_private(file_storage.stream)
    try:
        structure = _inspect_csv_structure(path)
        existing = HistoricalImport.query.filter_by(sha256=file_hash).first()
        if existing is not None:
            raise ConflictError(
                "Este archivo ya fue importado anteriormente; el hash es único incluso "
                "si el lote fue revertido."
            )

        historical_import = HistoricalImport(
            original_filename=original_filename,
            storage_key=storage_key,
            file_size_bytes=size,
            sha256=file_hash,
            file_format="csv",
            source_system=normalized_source,
            document_type=normalized_document_type,
            file_encoding=CSV_ENCODING,
            delimiter=CSV_DELIMITER,
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            schema_version="historical-csv-v1",
            mapping_version="mapping-v1",
            validation_version="validation-v1",
            fingerprint_version=FINGERPRINT_VERSION,
            metadata_json=structure,
            status=IMPORT_STATUS_UPLOADED,
            created_by_user_id=actor_user_id,
        )
        db.session.add(historical_import)
        db.session.commit()
        return historical_import
    except IntegrityError as exc:
        db.session.rollback()
        path.unlink(missing_ok=True)
        raise ConflictError(
            "Este archivo ya fue importado por otra solicitud concurrente."
        ) from exc
    except Exception:
        db.session.rollback()
        path.unlink(missing_ok=True)
        raise


def _canonical_public_id(public_id: str) -> str:
    try:
        return str(UUID(public_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise NotFoundError("El lote histórico solicitado no existe.") from exc


def get_import_or_404(public_id: str) -> HistoricalImport:
    historical_import = HistoricalImport.query.filter_by(
        public_id=_canonical_public_id(public_id)
    ).first()
    if historical_import is None:
        raise NotFoundError("El lote histórico solicitado no existe.")
    return historical_import


def _get_locked_import(public_id: str) -> HistoricalImport:
    historical_import = (
        db.session.query(HistoricalImport)
        .filter(HistoricalImport.public_id == _canonical_public_id(public_id))
        .with_for_update()
        .first()
    )
    if historical_import is None:
        raise NotFoundError("El lote histórico solicitado no existe.")
    return historical_import


def _verify_private_file(historical_import: HistoricalImport) -> Path:
    path = _storage_path(historical_import.storage_key)
    if not path.is_file():
        raise ConflictError("El archivo privado del lote ya no está disponible.")
    digest, size = sha256_file(path)
    if size != historical_import.file_size_bytes or not hmac.compare_digest(
        digest, historical_import.sha256
    ):
        raise ConflictError(
            "El archivo privado no coincide con el hash y tamaño registrados."
        )
    return path


def _issue_mapping(
    *,
    historical_import_id: int,
    source_row_number: int | None,
    field_name: str | None,
    error_code: str,
    severity: str,
    message: str,
    record_id: int | None = None,
    resolved: bool = False,
    resolved_by_user_id: int | None = None,
    resolved_at: datetime | None = None,
    resolution_action: str | None = None,
) -> dict:
    if severity == SEVERITY_WARNING:
        resolution_status = RESOLUTION_NOT_REQUIRED
    elif resolved:
        resolution_status = RESOLUTION_RESOLVED
    else:
        resolution_status = RESOLUTION_UNRESOLVED
    return {
        "historical_import_id": historical_import_id,
        "historical_demand_record_id": record_id,
        "source_row_number": source_row_number,
        "field_name": field_name,
        "error_code": error_code,
        "severity": severity,
        "message": message,
        # Nunca se persiste el valor fuente dentro del hallazgo. Si una versión
        # futura necesita contexto, deberá ser una representación redactada.
        "redacted_value": None,
        "resolution_status": resolution_status,
        "resolved": resolution_status == RESOLUTION_RESOLVED,
        "resolution_action": resolution_action if resolved else None,
        "resolution_note": None,
        "resolved_by_user_id": resolved_by_user_id if resolved else None,
        "resolved_at": resolved_at if resolved else None,
    }


def _bulk_insert(model, rows: list[dict], batch_size: int = 500) -> None:
    for start in range(0, len(rows), batch_size):
        db.session.bulk_insert_mappings(model, rows[start : start + batch_size])


def _clear_staging(historical_import: HistoricalImport) -> None:
    HistoricalImportError.query.filter_by(
        historical_import_id=historical_import.id
    ).delete(synchronize_session=False)
    HistoricalDemandRecord.query.filter_by(
        historical_import_id=historical_import.id
    ).update(
        {
            HistoricalDemandRecord.related_record_id: None,
            HistoricalDemandRecord.superseded_by_record_id: None,
        },
        synchronize_session=False,
    )
    HistoricalDemandRecord.query.filter_by(
        historical_import_id=historical_import.id
    ).delete(synchronize_session=False)
    db.session.flush()


def _parse_preview_file(
    historical_import: HistoricalImport,
    path: Path,
    requested_mapping: dict[str, str] | None,
) -> tuple[dict[str, str], int]:
    records_batch: list[dict] = []
    issues_batch: list[dict] = []
    total_rows = 0
    valid_rows = 0

    with _CSV_LIMIT_LOCK:
        previous_limit = csv.field_size_limit()
        csv.field_size_limit(MAX_CELL_CHARS)
        try:
            with path.open("r", encoding=CSV_ENCODING, newline="") as source:
                reader = csv.reader(
                    source,
                    delimiter=CSV_DELIMITER,
                    quotechar='"',
                    strict=True,
                )
                try:
                    raw_headers = next(reader)
                except StopIteration as exc:
                    raise _api_error("El CSV no contiene encabezados.", 422) from exc
                clean_headers = validate_headers(raw_headers)
                mapping = resolve_column_mapping(clean_headers, requested_mapping)

                ignored_columns = len(set(clean_headers) - set(mapping.values()))
                if ignored_columns:
                    issues_batch.append(
                        _issue_mapping(
                            historical_import_id=historical_import.id,
                            source_row_number=None,
                            field_name=None,
                            error_code="unmapped_columns_ignored",
                            severity=SEVERITY_WARNING,
                            message=(
                                f"Se ignoraron {ignored_columns} columnas no mapeadas; "
                                "no se conservaron en raw_row_json."
                            ),
                        )
                    )

                for source_row_number, row in enumerate(reader, start=2):
                    total_rows += 1
                    if total_rows > MAX_ROWS:
                        raise _api_error(
                            f"El CSV excede el máximo de {MAX_ROWS} filas.", 413
                        )

                    if not row or all(not strip_external(cell) for cell in row):
                        issues_batch.append(
                            _issue_mapping(
                                historical_import_id=historical_import.id,
                                source_row_number=source_row_number,
                                field_name=None,
                                error_code="blank_row_ignored",
                                severity=SEVERITY_WARNING,
                                message="La fila vacía fue ignorada.",
                            )
                        )
                        continue

                    if len(row) != len(clean_headers):
                        issues_batch.append(
                            _issue_mapping(
                                historical_import_id=historical_import.id,
                                source_row_number=source_row_number,
                                field_name=None,
                                error_code="column_count_mismatch",
                                severity=SEVERITY_ERROR,
                                message=(
                                    "La cantidad de celdas no coincide con los encabezados."
                                ),
                            )
                        )
                        continue

                    raw_row = canonicalize_csv_row(clean_headers, row, mapping)
                    parsed, row_issues = validate_historical_row(raw_row)
                    if row_issues:
                        for issue in row_issues:
                            issues_batch.append(
                                _issue_mapping(
                                    historical_import_id=historical_import.id,
                                    source_row_number=source_row_number,
                                    field_name=issue.field_name,
                                    error_code=issue.error_code,
                                    severity=issue.severity,
                                    message=issue.message,
                                )
                            )
                        continue

                    assert parsed is not None
                    fingerprint = build_fingerprint(
                        source_system=historical_import.source_system,
                        document_type=historical_import.document_type,
                        document_number_normalized=parsed.values[
                            "document_number_normalized"
                        ],
                        source_line_id_normalized=parsed.values[
                            "source_line_id_normalized"
                        ],
                        event_date=parsed.values["event_date"],
                        product_code_normalized=parsed.values[
                            "product_code_normalized"
                        ],
                        quantity=parsed.values["quantity"],
                        record_type=parsed.values["record_type"],
                        version=historical_import.fingerprint_version,
                    )
                    record_values = dict(parsed.values)
                    record_values.update(
                        {
                            "historical_import_id": historical_import.id,
                            "source_row_number": source_row_number,
                            "document_type": historical_import.document_type,
                            "fingerprint": fingerprint.value,
                            "fingerprint_strength": fingerprint.strength,
                            "match_status": "pending",
                            "include_in_demand": False,
                            "raw_row_json": parsed.raw_row,
                        }
                    )
                    records_batch.append(record_values)
                    valid_rows += 1

                    if len(records_batch) >= 500:
                        _bulk_insert(HistoricalDemandRecord, records_batch)
                        records_batch.clear()
                    if len(issues_batch) >= 500:
                        _bulk_insert(HistoricalImportError, issues_batch)
                        issues_batch.clear()

                if valid_rows == 0:
                    issues_batch.append(
                        _issue_mapping(
                            historical_import_id=historical_import.id,
                            source_row_number=None,
                            field_name=None,
                            error_code="no_valid_rows",
                            severity=SEVERITY_ERROR,
                            message="El CSV no contiene filas históricas válidas.",
                        )
                    )
                if records_batch:
                    _bulk_insert(HistoricalDemandRecord, records_batch)
                if issues_batch:
                    _bulk_insert(HistoricalImportError, issues_batch)
                return mapping, total_rows
        except csv.Error as exc:
            raise _api_error(
                "El CSV está mal formado o una celda excede el límite permitido.",
                422,
            ) from exc
        except HeaderValidationError as exc:
            raise _api_error(str(exc), 422) from exc
        finally:
            csv.field_size_limit(previous_limit)


def _record_flags(record: HistoricalDemandRecord) -> dict[str, bool]:
    source = record.review_flags_json or {}
    if not isinstance(source, dict):
        return {}
    return {key: bool(source.get(key)) for key in _REVIEW_FLAGS}


def _resolved_review_issue(
    historical_import: HistoricalImport,
    record: HistoricalDemandRecord,
    *,
    field_name: str | None,
    error_code: str,
    message: str,
    flag: str,
    action: str,
) -> dict:
    resolved = bool(_record_flags(record).get(flag))
    return _issue_mapping(
        historical_import_id=historical_import.id,
        source_row_number=record.source_row_number,
        field_name=field_name,
        error_code=error_code,
        severity=SEVERITY_REVIEW,
        message=message,
        record_id=record.id,
        resolved=resolved,
        resolved_by_user_id=record.reviewed_by_user_id,
        resolved_at=record.reviewed_at,
        resolution_action=action,
    )


def _chunks(values: list, size: int = 500):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _load_existing_related_candidates(
    records: list[HistoricalDemandRecord],
) -> dict[tuple[str, str], list[HistoricalDemandRecord]]:
    documents = sorted(
        {
            record.document_number_normalized
            for record in records
            if record.document_number_normalized
            and record.record_type != RECORD_TYPE_SALE
        }
    )
    result: dict[tuple[str, str], list[HistoricalDemandRecord]] = defaultdict(list)
    if not documents:
        return result

    for document_chunk in _chunks(documents):
        candidates = (
            HistoricalDemandRecord.query.join(
                HistoricalImport,
                HistoricalImport.id
                == HistoricalDemandRecord.historical_import_id,
            )
            .filter(
                HistoricalImport.status == IMPORT_STATUS_CONFIRMED,
                HistoricalDemandRecord.document_number_normalized.in_(
                    document_chunk
                ),
                HistoricalDemandRecord.record_type.in_(
                    (RECORD_TYPE_SALE, RECORD_TYPE_CORRECTION)
                ),
                HistoricalDemandRecord.effective_status.in_(
                    (RECORD_STATUS_ISSUED, RECORD_STATUS_ACTIVE)
                ),
                HistoricalDemandRecord.superseded_by_record_id.is_(None),
            )
            .all()
        )
        for candidate in candidates:
            key = (
                candidate.document_number_normalized,
                candidate.product_code_normalized,
            )
            result[key].append(candidate)
    return result


def _valid_related_target(
    record: HistoricalDemandRecord,
    target: HistoricalDemandRecord,
    historical_import: HistoricalImport,
) -> bool:
    if target.id == record.id:
        return False
    if target.record_type not in (RECORD_TYPE_SALE, RECORD_TYPE_CORRECTION):
        return False
    if target.document_number_normalized != record.document_number_normalized:
        return False
    if target.product_code_normalized != record.product_code_normalized:
        return False
    if target.effective_status not in ACTIVE_RECORD_STATUSES:
        return False
    if target.superseded_by_record_id is not None:
        return False
    if target.historical_import_id == historical_import.id:
        return target.source_row_number < record.source_row_number
    return (
        target.historical_import is not None
        and target.historical_import.status == IMPORT_STATUS_CONFIRMED
    )


def _rebuild_dynamic_issues(historical_import: HistoricalImport) -> None:
    HistoricalImportError.query.filter(
        HistoricalImportError.historical_import_id == historical_import.id,
        HistoricalImportError.error_code.in_(_DYNAMIC_ERROR_CODES),
    ).delete(synchronize_session=False)

    records = (
        HistoricalDemandRecord.query.filter_by(
            historical_import_id=historical_import.id
        )
        .order_by(HistoricalDemandRecord.source_row_number.asc())
        .populate_existing()
        .all()
    )
    products = Product.query.order_by(Product.id.asc()).populate_existing().all()
    product_by_id = {product.id: product for product in products}
    indexes = build_product_indexes(products)
    issues: list[dict] = []

    if indexes.normalized_code_collisions:
        issues.append(
            _issue_mapping(
                historical_import_id=historical_import.id,
                source_row_number=None,
                field_name="product_code",
                error_code="product_code_collision_critical",
                severity=SEVERITY_ERROR,
                message=(
                    "Existen productos operativos que colisionan tras normalizar "
                    "sus códigos; el lote no puede confirmarse."
                ),
            )
        )

    for record in records:
        flags = _record_flags(record)
        manually_selected = record.match_method in {
            "manual_name_admin",
            "manual_exact_admin",
        }
        if manually_selected and record.product_id in product_by_id:
            selected_product = product_by_id[record.product_id]
            selected_code_matches = (
                normalize_code(selected_product.code)
                == record.product_code_normalized
            )
            selected_name_matches = bool(
                record.product_name_normalized
                and normalize_name(selected_product.name)
                == record.product_name_normalized
            )
            selection_still_valid = (
                record.match_method == "manual_exact_admin"
                and selected_code_matches
            ) or (
                record.match_method == "manual_name_admin"
                and selected_name_matches
            )
            if not selection_still_valid:
                flags["manual_match"] = False
                flags["inactive_product"] = False
                record.review_flags_json = flags
                manually_selected = False
        if manually_selected and record.product_id in product_by_id:
            product = product_by_id[record.product_id]
            record.match_status = (
                "manual_inactive_approved"
                if not product.is_active and flags.get("inactive_product")
                else "manual_confirmed"
            )
            issues.append(
                _issue_mapping(
                    historical_import_id=historical_import.id,
                    source_row_number=record.source_row_number,
                    field_name="product_code",
                    error_code="manual_product_match",
                    severity=SEVERITY_REVIEW,
                    message="El enlace de producto fue confirmado manualmente por un administrador.",
                    record_id=record.id,
                    resolved=True,
                    resolved_by_user_id=record.reviewed_by_user_id,
                    resolved_at=record.reviewed_at,
                    resolution_action="admin_manual_match",
                )
            )
        else:
            record.product_id = None
            record.suggested_product_id = None
            record.match_method = None
            match = match_product(
                record.product_code_normalized,
                record.product_name_normalized,
                indexes,
            )
            if match.status == "code_collision":
                record.match_status = "code_collision"
                issues.append(
                    _issue_mapping(
                        historical_import_id=historical_import.id,
                        source_row_number=record.source_row_number,
                        field_name="product_code",
                        error_code="product_code_collision",
                        severity=SEVERITY_ERROR,
                        message="El código normalizado coincide con más de un producto.",
                        record_id=record.id,
                    )
                )
            elif match.status in {"exact", "exact_inactive"}:
                assert match.product is not None
                record.product_id = match.product.id
                record.match_method = "exact_code"
                record.match_status = (
                    "inactive_review"
                    if match.status == "exact_inactive"
                    else "exact"
                )
            elif match.status == "name_suggestion":
                assert match.suggested_product is not None
                record.suggested_product_id = match.suggested_product.id
                record.match_status = "name_suggested"
                issues.append(
                    _issue_mapping(
                        historical_import_id=historical_import.id,
                        source_row_number=record.source_row_number,
                        field_name="product_name",
                        error_code="product_name_suggestion",
                        severity=SEVERITY_REVIEW,
                        message=(
                            "Solo existe una sugerencia por nombre; requiere "
                            "selección explícita de un administrador."
                        ),
                        record_id=record.id,
                    )
                )
            else:
                record.match_status = "unmatched"
                issues.append(
                    _issue_mapping(
                        historical_import_id=historical_import.id,
                        source_row_number=record.source_row_number,
                        field_name="product_code",
                        error_code="product_unmatched",
                        severity=SEVERITY_REVIEW,
                        message=(
                            "No existe coincidencia exacta por código; un administrador "
                            "debe revisar el producto."
                        ),
                        record_id=record.id,
                    )
                )

        if record.product_id is not None:
            product = product_by_id.get(record.product_id)
            if product is None:
                record.match_status = "unmatched"
                issues.append(
                    _issue_mapping(
                        historical_import_id=historical_import.id,
                        source_row_number=record.source_row_number,
                        field_name="product_code",
                        error_code="product_unmatched",
                        severity=SEVERITY_REVIEW,
                        message="El producto enlazado ya no está disponible.",
                        record_id=record.id,
                    )
                )
            else:
                if not product.is_active:
                    issues.append(
                        _resolved_review_issue(
                            historical_import,
                            record,
                            field_name="product_code",
                            error_code="product_inactive",
                            message=(
                                "El producto está inactivo y requiere aprobación "
                                "administrativa auditada."
                            ),
                            flag="inactive_product",
                            action="admin_approved_inactive_product",
                        )
                    )
                if (
                    record.product_name_normalized
                    and record.product_name_normalized != normalize_name(product.name)
                ):
                    issues.append(
                        _issue_mapping(
                            historical_import_id=historical_import.id,
                            source_row_number=record.source_row_number,
                            field_name="product_name",
                            error_code="product_name_mismatch",
                            severity=SEVERITY_WARNING,
                            message=(
                                "El nombre histórico difiere del producto enlazado; "
                                "prevalece el código exacto."
                            ),
                            record_id=record.id,
                        )
                    )

    fingerprints = Counter(
        record.fingerprint
        for record in records
        if record.fingerprint_strength == "strong"
    )
    existing_dedupe: set[str] = set()
    strong_values = list(fingerprints)
    for fingerprint_chunk in _chunks(strong_values):
        existing_dedupe.update(
            value
            for (value,) in db.session.query(HistoricalDemandRecord.dedupe_key)
            .filter(HistoricalDemandRecord.dedupe_key.in_(fingerprint_chunk))
            .all()
            if value
        )

    for record in records:
        if record.fingerprint_strength == "weak":
            issues.append(
                _resolved_review_issue(
                    historical_import,
                    record,
                    field_name=None,
                    error_code="weak_possible_duplicate",
                    message=(
                        "Falta document_number o source_line_id: la fila es un posible "
                        "duplicado débil y no tendrá dedupe automática."
                    ),
                    flag="weak_duplicate",
                    action="admin_accepted_weak_duplicate",
                )
            )
        elif fingerprints[record.fingerprint] > 1:
            issues.append(
                _issue_mapping(
                    historical_import_id=historical_import.id,
                    source_row_number=record.source_row_number,
                    field_name=None,
                    error_code="strong_duplicate_in_file",
                    severity=SEVERITY_ERROR,
                    message="La fila tiene un fingerprint fuerte repetido dentro del archivo.",
                    record_id=record.id,
                )
            )
        elif record.fingerprint in existing_dedupe:
            issues.append(
                _issue_mapping(
                    historical_import_id=historical_import.id,
                    source_row_number=record.source_row_number,
                    field_name=None,
                    error_code="strong_duplicate_existing",
                    severity=SEVERITY_ERROR,
                    message=(
                        "La fila ya existe en un lote confirmado o revertido y no "
                        "puede reimportarse."
                    ),
                    record_id=record.id,
                )
            )

    staged_by_id = {record.id: record for record in records}
    staged_candidates: dict[
        tuple[str, str], list[HistoricalDemandRecord]
    ] = defaultdict(list)
    for record in records:
        if (
            record.document_number_normalized
            and record.record_type in (RECORD_TYPE_SALE, RECORD_TYPE_CORRECTION)
            and record.effective_status in ACTIVE_RECORD_STATUSES
        ):
            staged_candidates[
                (
                    record.document_number_normalized,
                    record.product_code_normalized,
                )
            ].append(record)

    existing_candidates = _load_existing_related_candidates(records)
    external_ids = {
        record.related_record_id
        for record in records
        if record.related_record_id
        and record.related_record_id not in staged_by_id
    }
    external_by_id: dict[int, HistoricalDemandRecord] = {}
    if external_ids:
        external_by_id = {
            record.id: record
            for record in HistoricalDemandRecord.query.filter(
                HistoricalDemandRecord.id.in_(external_ids)
            ).all()
        }
    for candidate_list in existing_candidates.values():
        for candidate in candidate_list:
            external_by_id[candidate.id] = candidate

    correction_targets: dict[int, list[HistoricalDemandRecord]] = defaultdict(list)
    for record in records:
        if record.record_type == RECORD_TYPE_SALE:
            record.related_record_id = None
            record.related_source_record_id = None
            record.related_document_number_normalized = None
            continue

        key = (
            record.document_number_normalized or "",
            record.product_code_normalized,
        )
        chosen: HistoricalDemandRecord | None = None
        manual_relationship = bool(_record_flags(record).get("relationship"))
        if manual_relationship and record.related_record_id:
            candidate = staged_by_id.get(record.related_record_id) or external_by_id.get(
                record.related_record_id
            )
            if candidate and _valid_related_target(
                record, candidate, historical_import
            ):
                chosen = candidate
                issues.append(
                    _issue_mapping(
                        historical_import_id=historical_import.id,
                        source_row_number=record.source_row_number,
                        field_name="document_number",
                        error_code="manual_relationship",
                        severity=SEVERITY_REVIEW,
                        message=(
                            "La relación con el documento reemplazado/original fue "
                            "confirmada manualmente."
                        ),
                        record_id=record.id,
                        resolved=True,
                        resolved_by_user_id=record.reviewed_by_user_id,
                        resolved_at=record.reviewed_at,
                        resolution_action="admin_selected_related_record",
                    )
                )
            else:
                record.related_record_id = None
                record.related_source_record_id = None
                record.related_document_number_normalized = None
                issues.append(
                    _issue_mapping(
                        historical_import_id=historical_import.id,
                        source_row_number=record.source_row_number,
                        field_name="document_number",
                        error_code="related_record_invalid",
                        severity=SEVERITY_REVIEW,
                        message="La relación seleccionada ya no es válida.",
                        record_id=record.id,
                    )
                )
        else:
            candidates = [
                candidate
                for candidate in staged_candidates.get(key, [])
                if candidate.source_row_number < record.source_row_number
                and candidate.id != record.id
            ]
            candidates.extend(existing_candidates.get(key, []))
            unique_candidates = {candidate.id: candidate for candidate in candidates}
            if len(unique_candidates) == 1:
                chosen = next(iter(unique_candidates.values()))
            elif len(unique_candidates) == 0:
                issues.append(
                    _issue_mapping(
                        historical_import_id=historical_import.id,
                        source_row_number=record.source_row_number,
                        field_name="document_number",
                        error_code="related_record_missing",
                        severity=SEVERITY_REVIEW,
                        message=(
                            "No se encontró una venta/corrección vigente y exacta "
                            "para el documento y producto."
                        ),
                        record_id=record.id,
                    )
                )
            else:
                issues.append(
                    _issue_mapping(
                        historical_import_id=historical_import.id,
                        source_row_number=record.source_row_number,
                        field_name="document_number",
                        error_code="related_record_ambiguous",
                        severity=SEVERITY_REVIEW,
                        message=(
                            "Hay más de una venta/corrección candidata; un administrador "
                            "debe seleccionar la relación."
                        ),
                        record_id=record.id,
                    )
                )

        if chosen is not None:
            record.related_record_id = chosen.id
            record.related_source_record_id = chosen.source_record_id_normalized
            record.related_document_number_normalized = (
                chosen.document_number_normalized
            )
            if record.record_type == RECORD_TYPE_CORRECTION:
                correction_targets[chosen.id].append(record)
        elif not manual_relationship:
            record.related_record_id = None
            record.related_source_record_id = None
            record.related_document_number_normalized = None

    for target_id, corrections in correction_targets.items():
        if len(corrections) > 1:
            for correction in corrections:
                issues.append(
                    _issue_mapping(
                        historical_import_id=historical_import.id,
                        source_row_number=correction.source_row_number,
                        field_name="document_number",
                        error_code="correction_target_repeated",
                        severity=SEVERITY_ERROR,
                        message=(
                            "Más de una corrección intenta reemplazar el mismo registro."
                        ),
                        record_id=correction.id,
                    )
                )

    effect_records = [
        record
        for record in records
        if record.record_type in (RECORD_TYPE_RETURN, RECORD_TYPE_CANCELLATION)
        and record.record_status in ACTIVE_RECORD_STATUSES
        and record.related_record_id
    ]
    target_ids = sorted({record.related_record_id for record in effect_records})
    prior_totals: dict[int, Decimal] = defaultdict(lambda: Decimal("0.00"))
    if target_ids:
        for id_chunk in _chunks(target_ids):
            prior_effects = (
                HistoricalDemandRecord.query.join(
                    HistoricalImport,
                    HistoricalImport.id
                    == HistoricalDemandRecord.historical_import_id,
                )
                .filter(
                    HistoricalImport.status == IMPORT_STATUS_CONFIRMED,
                    HistoricalDemandRecord.related_record_id.in_(id_chunk),
                    HistoricalDemandRecord.record_type.in_(
                        (RECORD_TYPE_RETURN, RECORD_TYPE_CANCELLATION)
                    ),
                    HistoricalDemandRecord.effective_status.in_(
                        (RECORD_STATUS_ISSUED, RECORD_STATUS_ACTIVE)
                    ),
                )
                .all()
            )
            for effect in prior_effects:
                prior_totals[effect.related_record_id] += Decimal(effect.quantity)

    staged_totals: dict[int, Decimal] = defaultdict(lambda: Decimal("0.00"))
    staged_effects_by_target: dict[int, list[HistoricalDemandRecord]] = defaultdict(list)
    for record in effect_records:
        staged_totals[record.related_record_id] += Decimal(record.quantity)
        staged_effects_by_target[record.related_record_id].append(record)

    all_targets = dict(external_by_id)
    all_targets.update(staged_by_id)
    for target_id, staged_total in staged_totals.items():
        target = all_targets.get(target_id)
        if target is None:
            continue
        total = prior_totals[target_id] + staged_total
        if total > Decimal(target.quantity):
            for record in staged_effects_by_target[target_id]:
                issues.append(
                    _issue_mapping(
                        historical_import_id=historical_import.id,
                        source_row_number=record.source_row_number,
                        field_name="quantity",
                        error_code="related_quantity_exceeded",
                        severity=SEVERITY_ERROR,
                        message=(
                            "Las devoluciones/cancelaciones exceden la cantidad "
                            "de la venta o corrección relacionada."
                        ),
                        record_id=record.id,
                    )
                )

    for record in records:
        if (
            record.record_type == RECORD_TYPE_CORRECTION
            and record.record_status in ACTIVE_RECORD_STATUSES
            and record.related_record_id
        ):
            consumed = (
                prior_totals[record.related_record_id]
                + staged_totals[record.related_record_id]
            )
            if consumed > Decimal(record.quantity):
                issues.append(
                    _resolved_review_issue(
                        historical_import,
                        record,
                        field_name="quantity",
                        error_code="negative_net_demand",
                        message=(
                            "La corrección dejaría demanda neta negativa y requiere "
                            "revisión administrativa explícita."
                        ),
                        flag="negative_net",
                        action="admin_accepted_negative_net",
                    )
                )

    # Revisión de demanda neta por producto y mes institucional. Las fechas son
    # Date (sin hora) y el período autorizado es exclusivamente 2025, por lo que
    # no existe conversión ambigua de zona horaria en esta versión.
    product_ids = sorted({record.product_id for record in records if record.product_id})
    projected_superseded_ids = {
        record.related_record_id
        for record in records
        if record.record_type == RECORD_TYPE_CORRECTION
        and record.record_status in ACTIVE_RECORD_STATUSES
        and record.related_record_id
    }
    period_totals: dict[tuple[int, int, int], Decimal] = defaultdict(
        lambda: Decimal("0.00")
    )

    def demand_delta(record: HistoricalDemandRecord) -> Decimal:
        amount = Decimal(record.quantity)
        if record.record_type in (RECORD_TYPE_RETURN, RECORD_TYPE_CANCELLATION):
            return -amount
        return amount

    if product_ids:
        for id_chunk in _chunks(product_ids):
            existing_period_records = (
                HistoricalDemandRecord.query.join(
                    HistoricalImport,
                    HistoricalImport.id
                    == HistoricalDemandRecord.historical_import_id,
                )
                .filter(
                    HistoricalImport.status == IMPORT_STATUS_CONFIRMED,
                    HistoricalDemandRecord.product_id.in_(id_chunk),
                    HistoricalDemandRecord.include_in_demand.is_(True),
                    HistoricalDemandRecord.effective_status.in_(
                        ACTIVE_RECORD_STATUSES
                    ),
                )
                .all()
            )
            for existing in existing_period_records:
                if existing.id in projected_superseded_ids:
                    continue
                key = (
                    existing.product_id,
                    existing.event_date.year,
                    existing.event_date.month,
                )
                period_totals[key] += demand_delta(existing)

    staged_by_period: dict[
        tuple[int, int, int], list[HistoricalDemandRecord]
    ] = defaultdict(list)
    for record in records:
        if (
            record.product_id is None
            or record.record_status not in ACTIVE_RECORD_STATUSES
            or record.id in projected_superseded_ids
        ):
            continue
        key = (record.product_id, record.event_date.year, record.event_date.month)
        period_totals[key] += demand_delta(record)
        staged_by_period[key].append(record)

    already_flagged_negative = {
        issue.get("historical_demand_record_id")
        for issue in issues
        if issue.get("error_code") == "negative_net_demand"
    }
    for key, staged_period_records in staged_by_period.items():
        if period_totals[key] >= Decimal("0.00"):
            continue
        candidates = sorted(
            staged_period_records,
            key=lambda item: (
                item.record_type
                not in (RECORD_TYPE_RETURN, RECORD_TYPE_CANCELLATION),
                item.source_row_number,
            ),
        )
        review_record = candidates[0]
        if review_record.id in already_flagged_negative:
            continue
        issues.append(
            _resolved_review_issue(
                historical_import,
                review_record,
                field_name="quantity",
                error_code="negative_net_demand",
                message=(
                    "La demanda neta mensual del producto quedaría negativa y "
                    "requiere revisión administrativa explícita."
                ),
                flag="negative_net",
                action="admin_accepted_negative_net",
            )
        )

    db.session.flush()
    if issues:
        _bulk_insert(HistoricalImportError, issues)
    db.session.flush()


def _refresh_counters(
    historical_import: HistoricalImport, *, total_rows: int | None = None
) -> None:
    def distinct_issue_rows(severity: str, *, unresolved_only: bool) -> int:
        filters = [
            HistoricalImportError.historical_import_id == historical_import.id,
            HistoricalImportError.severity == severity,
        ]
        if unresolved_only:
            filters.append(
                HistoricalImportError.resolution_status != RESOLUTION_RESOLVED
            )
        row_count = (
            db.session.query(
                func.count(func.distinct(HistoricalImportError.source_row_number))
            )
            .filter(*filters, HistoricalImportError.source_row_number.isnot(None))
            .scalar()
            or 0
        )
        # Un hallazgo global (por ejemplo una colisión de códigos operativos)
        # se representa como una sola unidad, aunque no pertenezca a una fila.
        has_global = (
            db.session.query(HistoricalImportError.id)
            .filter(*filters, HistoricalImportError.source_row_number.is_(None))
            .first()
            is not None
        )
        return int(row_count) + int(has_global)

    if total_rows is not None:
        historical_import.total_rows = total_rows
    historical_import.valid_rows = (
        db.session.query(func.count(HistoricalDemandRecord.id))
        .filter(
            HistoricalDemandRecord.historical_import_id == historical_import.id
        )
        .scalar()
        or 0
    )
    historical_import.error_count = distinct_issue_rows(
        SEVERITY_ERROR, unresolved_only=True
    )
    historical_import.warning_count = distinct_issue_rows(
        SEVERITY_WARNING, unresolved_only=False
    )
    historical_import.review_count = (
        db.session.query(
            func.count(func.distinct(HistoricalImportError.source_row_number))
        )
        .filter(
            HistoricalImportError.historical_import_id == historical_import.id,
            HistoricalImportError.error_code.in_(_PRODUCT_MATCH_ISSUE_CODES),
            HistoricalImportError.resolution_status != RESOLUTION_RESOLVED,
            HistoricalImportError.source_row_number.isnot(None),
        )
        .scalar()
        or 0
    )
    historical_import.matched_count = (
        db.session.query(func.count(HistoricalDemandRecord.id))
        .filter(
            HistoricalDemandRecord.historical_import_id == historical_import.id,
            HistoricalDemandRecord.product_id.isnot(None),
        )
        .scalar()
        or 0
    )
    historical_import.unmatched_count = (
        historical_import.valid_rows - historical_import.matched_count
    )
    historical_import.strong_fingerprint_count = (
        db.session.query(func.count(HistoricalDemandRecord.id))
        .filter(
            HistoricalDemandRecord.historical_import_id == historical_import.id,
            HistoricalDemandRecord.fingerprint_strength == "strong",
        )
        .scalar()
        or 0
    )
    historical_import.weak_fingerprint_count = (
        historical_import.valid_rows
        - historical_import.strong_fingerprint_count
    )


def preview_import(
    public_id: str,
    data: dict,
    *,
    actor_user_id: int,
) -> HistoricalImport:
    historical_import = _get_locked_import(public_id)
    if historical_import.status in (IMPORT_STATUS_CONFIRMED, IMPORT_STATUS_REVERTED):
        db.session.rollback()
        raise ConflictError("Un lote confirmado o revertido no puede reprocesarse.")

    path = _verify_private_file(historical_import)
    requested_mapping = (
        data.get("mapping")
        if "mapping" in data
        else historical_import.column_mapping_json
    )
    try:
        _clear_staging(historical_import)
        mapping, total_rows = _parse_preview_file(
            historical_import, path, requested_mapping
        )
        historical_import.column_mapping_json = mapping
        _rebuild_dynamic_issues(historical_import)
        _refresh_counters(historical_import, total_rows=total_rows)
        historical_import.status = IMPORT_STATUS_PREVIEWED
        historical_import.previewed_by_user_id = actor_user_id
        historical_import.previewed_at = datetime.utcnow()
        historical_import.dry_run_summary_json = None
        historical_import.confirmation_token_hash = None
        historical_import.confirmation_token_expires_at = None
        historical_import.confirmation_token_used_at = None
        historical_import.lock_version += 1
        db.session.commit()
        return historical_import
    except StaleDataError as exc:
        db.session.rollback()
        raise ConflictError(
            "El lote cambió concurrentemente; vuelva a cargar y reintente."
        ) from exc
    except Exception:
        db.session.rollback()
        raise


def _verify_staged_records(
    historical_import: HistoricalImport, path: Path
) -> None:
    mapping = historical_import.column_mapping_json
    if not isinstance(mapping, dict):
        raise ConflictError("El lote no tiene un mapping válido.")

    staged = {
        record.source_row_number: record
        for record in HistoricalDemandRecord.query.filter_by(
            historical_import_id=historical_import.id
        ).all()
    }
    seen_valid_rows: set[int] = set()
    total_rows = 0

    with _CSV_LIMIT_LOCK:
        previous_limit = csv.field_size_limit()
        csv.field_size_limit(MAX_CELL_CHARS)
        try:
            with path.open("r", encoding=CSV_ENCODING, newline="") as source:
                reader = csv.reader(
                    source,
                    delimiter=CSV_DELIMITER,
                    quotechar='"',
                    strict=True,
                )
                raw_headers = next(reader)
                clean_headers = validate_headers(raw_headers)
                resolved = resolve_column_mapping(clean_headers, mapping)
                if resolved != mapping:
                    raise ConflictError("El mapping persistido ya no es reproducible.")

                for source_row_number, row in enumerate(reader, start=2):
                    total_rows += 1
                    if not row or all(not strip_external(cell) for cell in row):
                        continue
                    if len(row) != len(clean_headers):
                        continue
                    raw_row = canonicalize_csv_row(clean_headers, row, mapping)
                    parsed, issues = validate_historical_row(raw_row)
                    if issues or parsed is None:
                        continue
                    fingerprint = build_fingerprint(
                        source_system=historical_import.source_system,
                        document_type=historical_import.document_type,
                        document_number_normalized=parsed.values[
                            "document_number_normalized"
                        ],
                        source_line_id_normalized=parsed.values[
                            "source_line_id_normalized"
                        ],
                        event_date=parsed.values["event_date"],
                        product_code_normalized=parsed.values[
                            "product_code_normalized"
                        ],
                        quantity=parsed.values["quantity"],
                        record_type=parsed.values["record_type"],
                        version=historical_import.fingerprint_version,
                    )
                    record = staged.get(source_row_number)
                    if record is None:
                        raise ConflictError(
                            "El staging no coincide con el contenido validado del archivo."
                        )
                    expected = (
                        fingerprint.value,
                        fingerprint.strength,
                        parsed.values["event_date"],
                        parsed.values["product_code_original"],
                        parsed.values["product_code_normalized"],
                        parsed.values["product_name_original"],
                        parsed.values["product_name_normalized"],
                        Decimal(parsed.values["quantity"]),
                        (
                            Decimal(parsed.values["unit_price"])
                            if parsed.values["unit_price"] is not None
                            else None
                        ),
                        parsed.values["record_type"],
                        parsed.values["record_status"],
                        parsed.values["document_number_original"],
                        parsed.values["document_number_normalized"],
                        parsed.values["source_record_id_original"],
                        parsed.values["source_record_id_normalized"],
                        parsed.values["source_line_id_original"],
                        parsed.values["source_line_id_normalized"],
                        parsed.raw_row,
                    )
                    actual = (
                        record.fingerprint,
                        record.fingerprint_strength,
                        record.event_date,
                        record.product_code_original,
                        record.product_code_normalized,
                        record.product_name_original,
                        record.product_name_normalized,
                        Decimal(record.quantity),
                        (
                            Decimal(record.unit_price)
                            if record.unit_price is not None
                            else None
                        ),
                        record.record_type,
                        record.record_status,
                        record.document_number_original,
                        record.document_number_normalized,
                        record.source_record_id_original,
                        record.source_record_id_normalized,
                        record.source_line_id_original,
                        record.source_line_id_normalized,
                        record.raw_row_json,
                    )
                    if expected != actual:
                        raise ConflictError(
                            "El staging no coincide con el contenido validado del archivo."
                        )
                    seen_valid_rows.add(source_row_number)
        except (csv.Error, HeaderValidationError, StopIteration) as exc:
            raise ConflictError(
                "El archivo ya no puede reproducirse con el mapping validado."
            ) from exc
        finally:
            csv.field_size_limit(previous_limit)

    if total_rows != historical_import.total_rows or seen_valid_rows != set(staged):
        raise ConflictError(
            "Los conteos del staging no coinciden con el archivo validado."
        )


def _blocking_issue_count(historical_import_id: int) -> int:
    return (
        db.session.query(func.count(HistoricalImportError.id))
        .filter(
            HistoricalImportError.historical_import_id == historical_import_id,
            HistoricalImportError.severity.in_(
                (SEVERITY_ERROR, SEVERITY_REVIEW)
            ),
            HistoricalImportError.resolution_status != RESOLUTION_RESOLVED,
        )
        .scalar()
        or 0
    )


def _dry_run_summary(historical_import: HistoricalImport) -> dict:
    records = HistoricalDemandRecord.query.filter_by(
        historical_import_id=historical_import.id
    ).all()
    active = [
        record
        for record in records
        if record.record_status in ACTIVE_RECORD_STATUSES
    ]
    superseded_targets = {
        record.related_record_id
        for record in active
        if record.record_type == RECORD_TYPE_CORRECTION
        and record.related_record_id
        and any(candidate.id == record.related_record_id for candidate in records)
    }

    totals = {
        RECORD_TYPE_SALE: Decimal("0.00"),
        RECORD_TYPE_RETURN: Decimal("0.00"),
        RECORD_TYPE_CANCELLATION: Decimal("0.00"),
        RECORD_TYPE_CORRECTION: Decimal("0.00"),
    }
    counts = Counter()
    for record in active:
        if record.id in superseded_targets:
            continue
        totals[record.record_type] += Decimal(record.quantity)
        counts[record.record_type] += 1
    net = (
        totals[RECORD_TYPE_SALE]
        + totals[RECORD_TYPE_CORRECTION]
        - totals[RECORD_TYPE_RETURN]
        - totals[RECORD_TYPE_CANCELLATION]
    )
    unresolved_reviews = (
        db.session.query(func.count(HistoricalImportError.id))
        .filter(
            HistoricalImportError.historical_import_id == historical_import.id,
            HistoricalImportError.severity == SEVERITY_REVIEW,
            HistoricalImportError.resolution_status != RESOLUTION_RESOLVED,
        )
        .scalar()
        or 0
    )
    possible_duplicates = (
        db.session.query(
            func.count(func.distinct(HistoricalImportError.source_row_number))
        )
        .filter(
            HistoricalImportError.historical_import_id == historical_import.id,
            HistoricalImportError.error_code.in_(_DUPLICATE_ERROR_CODES),
            HistoricalImportError.source_row_number.isnot(None),
        )
        .scalar()
        or 0
    )
    return {
        "rows": historical_import.total_rows,
        "valid_rows": historical_import.valid_rows,
        "warnings": historical_import.warning_count,
        "unresolved_reviews": unresolved_reviews,
        "pending_matches": historical_import.review_count,
        "possible_duplicates": possible_duplicates,
        "errors": historical_import.error_count,
        "matched": historical_import.matched_count,
        "unmatched": historical_import.unmatched_count,
        "strong_fingerprints": historical_import.strong_fingerprint_count,
        "weak_fingerprints": historical_import.weak_fingerprint_count,
        "active_counts": dict(counts),
        "quantities": {
            "sales": format(totals[RECORD_TYPE_SALE], "f"),
            "returns": format(totals[RECORD_TYPE_RETURN], "f"),
            "cancellations": format(totals[RECORD_TYPE_CANCELLATION], "f"),
            "corrections": format(totals[RECORD_TYPE_CORRECTION], "f"),
            "net_demand": format(net, "f"),
        },
        "activates_demand": False,
    }


def dry_run_import(
    public_id: str, *, actor_user_id: int
) -> tuple[HistoricalImport, str, dict]:
    historical_import = _get_locked_import(public_id)
    if historical_import.status not in (
        IMPORT_STATUS_PREVIEWED,
        IMPORT_STATUS_DRY_RUN_READY,
    ):
        db.session.rollback()
        raise ConflictError("Primero debe ejecutar preview sobre el lote.")

    try:
        path = _verify_private_file(historical_import)
        _verify_staged_records(historical_import, path)
        _rebuild_dynamic_issues(historical_import)
        _refresh_counters(historical_import)
        blockers = _blocking_issue_count(historical_import.id)
        if blockers:
            historical_import.status = IMPORT_STATUS_PREVIEWED
            historical_import.confirmation_token_hash = None
            historical_import.confirmation_token_expires_at = None
            historical_import.confirmation_token_used_at = None
            historical_import.lock_version += 1
            db.session.commit()
            raise _api_error(
                f"El dry run encontró {blockers} errores/revisiones sin resolver.",
                422,
            )

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        summary = _dry_run_summary(historical_import)
        historical_import.status = IMPORT_STATUS_DRY_RUN_READY
        historical_import.dry_run_summary_json = summary
        historical_import.dry_run_by_user_id = actor_user_id
        historical_import.dry_run_at = datetime.utcnow()
        historical_import.confirmation_token_hash = token_hash
        historical_import.confirmation_token_expires_at = (
            datetime.utcnow() + timedelta(minutes=CONFIRMATION_TOKEN_TTL_MINUTES)
        )
        historical_import.confirmation_token_used_at = None
        historical_import.lock_version += 1
        db.session.commit()
        return historical_import, token, summary
    except StaleDataError as exc:
        db.session.rollback()
        raise ConflictError(
            "El lote cambió concurrentemente; vuelva a cargar y reintente."
        ) from exc
    except ApiError:
        # Si el 422 ya se confirmó, rollback es inocuo; en los demás casos
        # garantiza que no queden cambios parciales en la sesión.
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        raise


def _validate_confirmation_token(token: object) -> tuple[str, str]:
    if not isinstance(token, str):
        raise ValidationError("El campo 'confirmation_token' es obligatorio.")
    candidate = token.strip()
    if not candidate or len(candidate) > 200:
        raise ValidationError("El token de confirmación no es válido.")
    return candidate, hashlib.sha256(candidate.encode("utf-8")).hexdigest()


def _lock_confirmation_dependencies(
    historical_import: HistoricalImport,
) -> tuple[list[HistoricalDemandRecord], dict[int, HistoricalDemandRecord]]:
    """Bloquea, en orden estable, todo estado que afecta una confirmación.

    Además del lote se bloquean productos, lotes fuente y registros candidatos.
    Así dos confirmaciones no pueden consumir simultáneamente la misma venta
    ni superseder el mismo registro usando una validación obsoleta.
    """
    # Matching y la detección de colisiones dependen del catálogo completo.
    db.session.query(Product).order_by(Product.id.asc()).with_for_update().execution_options(
        populate_existing=True
    ).all()

    staged_refs = (
        db.session.query(
            HistoricalDemandRecord.id,
            HistoricalDemandRecord.document_number_normalized,
            HistoricalDemandRecord.related_record_id,
        )
        .filter(
            HistoricalDemandRecord.historical_import_id == historical_import.id
        )
        .order_by(HistoricalDemandRecord.id.asc())
        .all()
    )
    staged_ids = {row.id for row in staged_refs}
    documents = sorted(
        {
            row.document_number_normalized
            for row in staged_refs
            if row.document_number_normalized
        }
    )
    external_pairs: set[tuple[int, int]] = set()
    for document_chunk in _chunks(documents):
        external_pairs.update(
            (record_id, import_id)
            for record_id, import_id in (
                db.session.query(
                    HistoricalDemandRecord.id,
                    HistoricalDemandRecord.historical_import_id,
                )
                .join(
                    HistoricalImport,
                    HistoricalImport.id
                    == HistoricalDemandRecord.historical_import_id,
                )
                .filter(
                    HistoricalDemandRecord.historical_import_id
                    != historical_import.id,
                    HistoricalImport.status == IMPORT_STATUS_CONFIRMED,
                    HistoricalDemandRecord.document_number_normalized.in_(
                        document_chunk
                    ),
                    HistoricalDemandRecord.record_type.in_(
                        (RECORD_TYPE_SALE, RECORD_TYPE_CORRECTION)
                    ),
                    HistoricalDemandRecord.effective_status.in_(
                        ACTIVE_RECORD_STATUSES
                    ),
                    HistoricalDemandRecord.superseded_by_record_id.is_(None),
                )
                .all()
            )
        )

    explicit_related_ids = {
        row.related_record_id
        for row in staged_refs
        if row.related_record_id and row.related_record_id not in staged_ids
    }
    if explicit_related_ids:
        external_pairs.update(
            (record_id, import_id)
            for record_id, import_id in db.session.query(
                HistoricalDemandRecord.id,
                HistoricalDemandRecord.historical_import_id,
            )
            .filter(HistoricalDemandRecord.id.in_(explicit_related_ids))
            .all()
        )

    external_import_ids = sorted({import_id for _, import_id in external_pairs})
    if external_import_ids:
        db.session.query(HistoricalImport).filter(
            HistoricalImport.id.in_(external_import_ids)
        ).order_by(HistoricalImport.id.asc()).with_for_update().execution_options(
            populate_existing=True
        ).all()

    all_record_ids = sorted(staged_ids | {record_id for record_id, _ in external_pairs})
    locked_records: dict[int, HistoricalDemandRecord] = {}
    for id_chunk in _chunks(all_record_ids):
        for record in (
            db.session.query(HistoricalDemandRecord)
            .filter(HistoricalDemandRecord.id.in_(id_chunk))
            .order_by(HistoricalDemandRecord.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
            .all()
        ):
            locked_records[record.id] = record

    staged = sorted(
        (locked_records[record_id] for record_id in staged_ids),
        key=lambda item: item.source_row_number,
    )
    related = {
        record_id: record
        for record_id, record in locked_records.items()
        if record_id not in staged_ids
    }
    return staged, related


def confirm_import(
    public_id: str,
    *,
    confirmation_token: object,
    actor_user_id: int,
) -> tuple[HistoricalImport, bool]:
    _, token_hash = _validate_confirmation_token(confirmation_token)
    historical_import = _get_locked_import(public_id)

    if historical_import.status == IMPORT_STATUS_CONFIRMED:
        if historical_import.confirmation_token_hash and hmac.compare_digest(
            token_hash, historical_import.confirmation_token_hash
        ):
            db.session.rollback()
            return historical_import, True
        db.session.rollback()
        raise ConflictError("El lote ya fue confirmado con otro token.")
    if historical_import.status == IMPORT_STATUS_REVERTED:
        db.session.rollback()
        raise ConflictError("Un lote revertido no puede confirmarse.")
    if historical_import.status != IMPORT_STATUS_DRY_RUN_READY:
        db.session.rollback()
        raise ConflictError("Debe ejecutar un dry run válido antes de confirmar.")
    if not historical_import.confirmation_token_hash or not hmac.compare_digest(
        token_hash, historical_import.confirmation_token_hash
    ):
        db.session.rollback()
        raise ConflictError("El token de confirmación no coincide.")
    if (
        historical_import.confirmation_token_expires_at is None
        or historical_import.confirmation_token_expires_at < datetime.utcnow()
    ):
        historical_import.status = IMPORT_STATUS_PREVIEWED
        historical_import.confirmation_token_hash = None
        historical_import.confirmation_token_expires_at = None
        historical_import.lock_version += 1
        db.session.commit()
        raise ConflictError("El token de confirmación expiró; ejecute dry run nuevamente.")

    try:
        path = _verify_private_file(historical_import)
        records, locked_related = _lock_confirmation_dependencies(historical_import)
        _verify_staged_records(historical_import, path)
        _rebuild_dynamic_issues(historical_import)
        _refresh_counters(historical_import)
        blockers = _blocking_issue_count(historical_import.id)
        if blockers:
            historical_import.status = IMPORT_STATUS_PREVIEWED
            historical_import.confirmation_token_hash = None
            historical_import.confirmation_token_expires_at = None
            historical_import.lock_version += 1
            db.session.commit()
            raise _api_error(
                "La confirmación fue bloqueada porque existen errores o revisiones "
                "sin resolver.",
                422,
            )

        if not records:
            raise _api_error("El lote no contiene filas válidas para confirmar.", 422)

        now = datetime.utcnow()
        record_by_id = {record.id: record for record in records}
        for record in records:
            if record.product_id is None:
                raise ConflictError("Una fila perdió su enlace de producto.")
            product = db.session.get(Product, record.product_id)
            if product is None:
                raise ConflictError("Una fila apunta a un producto inexistente.")
            flags = _record_flags(record)
            if not product.is_active:
                if not flags.get("inactive_product"):
                    raise ConflictError(
                        "Un producto inactivo no tiene aprobación administrativa."
                    )
            record.include_in_demand = (
                record.record_status in ACTIVE_RECORD_STATUSES
            )
            record.effective_status = record.record_status
            record.dedupe_key = (
                record.fingerprint
                if record.fingerprint_strength == "strong"
                else None
            )

        for correction in records:
            if (
                correction.record_type != RECORD_TYPE_CORRECTION
                or correction.record_status not in ACTIVE_RECORD_STATUSES
            ):
                continue
            target = record_by_id.get(
                correction.related_record_id
            ) or locked_related.get(correction.related_record_id)
            if target is None:
                raise ConflictError("Una corrección perdió su registro relacionado.")
            if (
                target.superseded_by_record_id is not None
                and target.superseded_by_record_id != correction.id
            ):
                raise ConflictError(
                    "El registro relacionado ya fue supersedido por otra corrección."
                )
            target.include_in_demand = False
            target.effective_status = RECORD_STATUS_SUPERSEDED
            target.superseded_by_record_id = correction.id
            target.superseded_by_import_id = historical_import.id
            target.superseded_at = now

        # Flush fuerza la restricción única de dedupe dentro de esta transacción.
        db.session.flush()
        historical_import.status = IMPORT_STATUS_CONFIRMED
        historical_import.confirmed_by_user_id = actor_user_id
        historical_import.confirmed_at = now
        historical_import.confirmation_token_used_at = now
        historical_import.lock_version += 1
        db.session.commit()
        return historical_import, False
    except (IntegrityError, StaleDataError) as exc:
        db.session.rollback()
        raise ConflictError(
            "Otra confirmación concurrente cambió el lote o uno de sus fingerprints."
        ) from exc
    except ApiError:
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        raise


def revert_import(
    public_id: str,
    *,
    reason: object,
    actor_user_id: int,
) -> tuple[HistoricalImport, bool]:
    if not isinstance(reason, str):
        raise ValidationError("El campo 'reason' es obligatorio.")
    safe_reason = strip_external(reason)
    if not safe_reason:
        raise ValidationError("El campo 'reason' es obligatorio.")
    if len(safe_reason) > 1000:
        raise ValidationError("El motivo no puede exceder 1000 caracteres.")
    if has_dangerous_cell_control(safe_reason) or starts_like_formula(safe_reason):
        raise ValidationError("El motivo contiene caracteres no permitidos.")

    historical_import = _get_locked_import(public_id)
    if historical_import.status == IMPORT_STATUS_REVERTED:
        db.session.rollback()
        return historical_import, True
    if historical_import.status != IMPORT_STATUS_CONFIRMED:
        db.session.rollback()
        raise ConflictError("Solo un lote confirmado puede revertirse.")

    try:
        own_records = (
            db.session.query(HistoricalDemandRecord)
            .filter(
                HistoricalDemandRecord.historical_import_id
                == historical_import.id
            )
            .order_by(HistoricalDemandRecord.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
            .all()
        )
        own_ids = {record.id for record in own_records}

        # No se puede revertir un origen que todavía sostiene devoluciones,
        # anulaciones o correcciones de otro lote confirmado.
        if own_ids:
            dependent = (
                db.session.query(HistoricalDemandRecord.id)
                .join(
                    HistoricalImport,
                    HistoricalImport.id
                    == HistoricalDemandRecord.historical_import_id,
                )
                .filter(
                    HistoricalDemandRecord.historical_import_id
                    != historical_import.id,
                    HistoricalImport.status == IMPORT_STATUS_CONFIRMED,
                    HistoricalDemandRecord.related_record_id.in_(own_ids),
                )
                .first()
            )
            if dependent is not None:
                raise ConflictError(
                    "El lote tiene registros relacionados en otro lote confirmado; "
                    "revierta primero el lote dependiente."
                )

        target_refs = (
            db.session.query(
                HistoricalDemandRecord.id,
                HistoricalDemandRecord.historical_import_id,
            )
            .filter(
                HistoricalDemandRecord.superseded_by_import_id
                == historical_import.id
            )
            .order_by(HistoricalDemandRecord.id.asc())
            .all()
        )
        target_import_ids = sorted(
            {
                import_id
                for _, import_id in target_refs
                if import_id != historical_import.id
            }
        )
        target_imports: dict[int, HistoricalImport] = {}
        if target_import_ids:
            target_imports = {
                item.id: item
                for item in db.session.query(HistoricalImport)
                .filter(HistoricalImport.id.in_(target_import_ids))
                .order_by(HistoricalImport.id.asc())
                .with_for_update()
                .execution_options(populate_existing=True)
                .all()
            }

        target_ids = [record_id for record_id, _ in target_refs]
        targets: list[HistoricalDemandRecord] = []
        for id_chunk in _chunks(target_ids):
            targets.extend(
                db.session.query(HistoricalDemandRecord)
                .filter(HistoricalDemandRecord.id.in_(id_chunk))
                .order_by(HistoricalDemandRecord.id.asc())
                .with_for_update()
                .execution_options(populate_existing=True)
                .all()
            )

        for target in targets:
            if (
                target.superseded_by_record_id not in own_ids
                or target.superseded_by_import_id != historical_import.id
            ):
                raise ConflictError(
                    "La cadena de corrección cambió durante la reversión."
                )
            target.superseded_by_record_id = None
            target.superseded_by_import_id = None
            target.superseded_at = None
            target.effective_status = target.record_status
            if target.historical_import_id == historical_import.id:
                target.include_in_demand = False
            else:
                source_batch = target_imports.get(target.historical_import_id)
                target.include_in_demand = bool(
                    source_batch
                    and source_batch.status == IMPORT_STATUS_CONFIRMED
                    and target.record_status in ACTIVE_RECORD_STATUSES
                )

        # La reversión es lógica: las filas y dedupe_key permanecen para
        # auditoría y para impedir reimportación, pero dejan de aportar demanda.
        for record in own_records:
            record.include_in_demand = False

        historical_import.status = IMPORT_STATUS_REVERTED
        historical_import.reversal_reason = safe_reason
        historical_import.reverted_by_user_id = actor_user_id
        historical_import.reverted_at = datetime.utcnow()
        historical_import.lock_version += 1
        db.session.commit()
        return historical_import, False
    except (IntegrityError, StaleDataError) as exc:
        db.session.rollback()
        raise ConflictError(
            "El lote cambió concurrentemente; vuelva a cargar y reintente."
        ) from exc
    except ApiError:
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        raise


def review_record(
    public_id: str,
    record_id: int,
    data: dict,
    *,
    actor_user_id: int,
) -> HistoricalDemandRecord:
    historical_import = _get_locked_import(public_id)
    if historical_import.status not in (
        IMPORT_STATUS_PREVIEWED,
        IMPORT_STATUS_DRY_RUN_READY,
    ):
        db.session.rollback()
        raise ConflictError("Solo se revisan filas de un lote no confirmado.")

    record = (
        db.session.query(HistoricalDemandRecord)
        .filter(
            HistoricalDemandRecord.id == record_id,
            HistoricalDemandRecord.historical_import_id == historical_import.id,
        )
        .with_for_update()
        .first()
    )
    if record is None:
        db.session.rollback()
        raise NotFoundError("La fila histórica solicitada no existe en este lote.")

    allowed_keys = {"product_id", "related_record_id", "approve"}
    if set(data) - allowed_keys:
        db.session.rollback()
        raise ValidationError("La revisión contiene campos no permitidos.")
    approvals = data.get("approve", [])
    if not isinstance(approvals, list) or any(
        not isinstance(value, str) for value in approvals
    ):
        db.session.rollback()
        raise ValidationError("'approve' debe ser una lista de códigos.")
    approval_set = set(approvals)
    if approval_set - _REVIEW_FLAGS:
        db.session.rollback()
        raise ValidationError("La revisión contiene aprobaciones no permitidas.")
    if (
        "manual_match" in approval_set
        and "product_id" not in data
        and record.match_method not in {"manual_name_admin", "manual_exact_admin"}
    ):
        db.session.rollback()
        raise ValidationError(
            "'manual_match' requiere seleccionar explícitamente product_id."
        )
    if (
        "relationship" in approval_set
        and "related_record_id" not in data
        and record.related_record_id is None
    ):
        db.session.rollback()
        raise ValidationError(
            "'relationship' requiere seleccionar explícitamente related_record_id."
        )
    if "weak_duplicate" in approval_set and record.fingerprint_strength != "weak":
        db.session.rollback()
        raise ValidationError(
            "'weak_duplicate' solo aplica a fingerprints débiles."
        )
    flags = _record_flags(record)
    for approval in approval_set:
        flags[approval] = True

    if "product_id" in data:
        try:
            product_id = int(data["product_id"])
        except (TypeError, ValueError) as exc:
            db.session.rollback()
            raise ValidationError("'product_id' debe ser entero.") from exc
        product = db.session.get(Product, product_id)
        if product is None:
            db.session.rollback()
            raise NotFoundError("El producto seleccionado no existe.")
        code_matches = normalize_code(product.code) == record.product_code_normalized
        name_matches = bool(
            record.product_name_normalized
            and normalize_name(product.name) == record.product_name_normalized
        )
        if not code_matches and not name_matches:
            db.session.rollback()
            raise _api_error(
                "El producto seleccionado no coincide por código exacto ni por "
                "la sugerencia normalizada de nombre.",
                422,
            )
        if not code_matches:
            flags["manual_match"] = True
            record.match_method = "manual_name_admin"
        else:
            record.match_method = "manual_exact_admin"
        record.product_id = product.id
        record.suggested_product_id = None

    if "related_record_id" in data:
        try:
            related_id = int(data["related_record_id"])
        except (TypeError, ValueError) as exc:
            db.session.rollback()
            raise ValidationError("'related_record_id' debe ser entero.") from exc
        target = db.session.get(HistoricalDemandRecord, related_id)
        if target is None:
            db.session.rollback()
            raise NotFoundError("El registro relacionado no existe.")
        if not _valid_related_target(record, target, historical_import):
            db.session.rollback()
            raise _api_error(
                "El registro relacionado no es una venta/corrección vigente del "
                "mismo documento y producto.",
                422,
            )
        flags["relationship"] = True
        record.related_record_id = target.id
        record.related_source_record_id = target.source_record_id_normalized
        record.related_document_number_normalized = target.document_number_normalized

    record.review_flags_json = flags
    record.reviewed_by_user_id = actor_user_id
    record.reviewed_at = datetime.utcnow()
    record.lock_version += 1
    historical_import.status = IMPORT_STATUS_PREVIEWED
    historical_import.confirmation_token_hash = None
    historical_import.confirmation_token_expires_at = None
    historical_import.confirmation_token_used_at = None
    historical_import.dry_run_summary_json = None
    historical_import.lock_version += 1
    try:
        db.session.flush()
        _rebuild_dynamic_issues(historical_import)
        _refresh_counters(historical_import)
        db.session.commit()
        return record
    except StaleDataError as exc:
        db.session.rollback()
        raise ConflictError(
            "El lote cambió concurrentemente; vuelva a cargar y reintente."
        ) from exc
    except Exception:
        db.session.rollback()
        raise


def _parse_pagination(page_value, per_page_value) -> tuple[int, int]:
    try:
        page = int(page_value or 1)
        per_page = int(per_page_value or DEFAULT_PAGE_SIZE)
    except (TypeError, ValueError) as exc:
        raise ValidationError("'page' y 'per_page' deben ser enteros.") from exc
    if page < 1:
        raise ValidationError("'page' debe ser mayor o igual a 1.")
    if per_page < 1 or per_page > MAX_PAGE_SIZE:
        raise ValidationError(
            f"'per_page' debe estar entre 1 y {MAX_PAGE_SIZE}."
        )
    return page, per_page


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def serialize_import(
    historical_import: HistoricalImport, *, is_admin: bool
) -> dict:
    possible_duplicates = (
        db.session.query(
            func.count(func.distinct(HistoricalImportError.source_row_number))
        )
        .filter(
            HistoricalImportError.historical_import_id == historical_import.id,
            HistoricalImportError.error_code.in_(_DUPLICATE_ERROR_CODES),
            HistoricalImportError.source_row_number.isnot(None),
        )
        .scalar()
        or 0
    )
    result = {
        "id": historical_import.public_id,
        "status": historical_import.status,
        "source_system": historical_import.source_system,
        "schema_version": historical_import.schema_version,
        "parser_version": historical_import.schema_version,
        "period_start": _iso(historical_import.period_start),
        "period_end": _iso(historical_import.period_end),
        "counts": {
            "rows": historical_import.total_rows,
            "valid": historical_import.valid_rows,
            "errors": historical_import.error_count,
            "warnings": historical_import.warning_count,
            "reviews_pending": historical_import.review_count,
            "pending_matches": historical_import.review_count,
            "possible_duplicates": possible_duplicates,
            "matched": historical_import.matched_count,
            "unmatched": historical_import.unmatched_count,
            "strong_fingerprints": historical_import.strong_fingerprint_count,
            "weak_fingerprints": historical_import.weak_fingerprint_count,
        },
        "created_at": _iso(historical_import.created_at),
        "previewed_at": _iso(historical_import.previewed_at),
        "dry_run_at": _iso(historical_import.dry_run_at),
        "confirmed_at": _iso(historical_import.confirmed_at),
        "reverted_at": _iso(historical_import.reverted_at),
        "dry_run_summary": historical_import.dry_run_summary_json,
    }
    if is_admin:
        result["admin_metadata"] = {
            "original_filename": historical_import.original_filename,
            "file_size_bytes": historical_import.file_size_bytes,
            "file_format": historical_import.file_format,
            "sha256": historical_import.sha256,
            "document_type": historical_import.document_type,
            "encoding": historical_import.file_encoding,
            "delimiter": historical_import.delimiter,
            "mapping": historical_import.column_mapping_json,
            "mapping_version": historical_import.mapping_version,
            "validation_version": historical_import.validation_version,
            "fingerprint_version": historical_import.fingerprint_version,
            "metadata": historical_import.metadata_json,
            "created_by_user_id": historical_import.created_by_user_id,
            "previewed_by_user_id": historical_import.previewed_by_user_id,
            "dry_run_by_user_id": historical_import.dry_run_by_user_id,
            "confirmed_by_user_id": historical_import.confirmed_by_user_id,
            "reverted_by_user_id": historical_import.reverted_by_user_id,
            "reversal_reason": historical_import.reversal_reason,
            "confirmation_token_expires_at": _iso(
                historical_import.confirmation_token_expires_at
            ),
            "confirmation_token_used_at": _iso(
                historical_import.confirmation_token_used_at
            ),
            "lock_version": historical_import.lock_version,
        }
    return result


def serialize_record(
    record: HistoricalDemandRecord, *, is_admin: bool
) -> dict:
    result = {
        "id": record.id,
        "source_row_number": record.source_row_number,
        "event_date": _iso(record.event_date),
        "product_code": record.product_code_original,
        "product_code_normalized": record.product_code_normalized,
        "product_name": record.product_name_original,
        "product_id": record.product_id,
        "suggested_product_id": record.suggested_product_id,
        "quantity": format(Decimal(record.quantity), "f"),
        "unit_price": (
            format(Decimal(record.unit_price), "f")
            if record.unit_price is not None
            else None
        ),
        "record_type": record.record_type,
        "record_status": record.record_status,
        "effective_status": record.effective_status,
        "match_status": record.match_status,
        "match_method": record.match_method,
        "fingerprint_strength": record.fingerprint_strength,
        "include_in_demand": bool(record.include_in_demand),
        "related_record_id": record.related_record_id,
        "superseded_by_record_id": record.superseded_by_record_id,
        "created_at": _iso(record.created_at),
    }
    if is_admin:
        result["admin_metadata"] = {
            "source_record_id_original": record.source_record_id_original,
            "source_record_id_normalized": record.source_record_id_normalized,
            "source_line_id_original": record.source_line_id_original,
            "source_line_id_normalized": record.source_line_id_normalized,
            "document_type": record.document_type,
            "document_number_original": record.document_number_original,
            "document_number_normalized": record.document_number_normalized,
            "product_name_normalized": record.product_name_normalized,
            "related_source_record_id": record.related_source_record_id,
            "related_document_number_normalized": (
                record.related_document_number_normalized
            ),
            "fingerprint": record.fingerprint,
            "dedupe_key": record.dedupe_key,
            "review_flags": record.review_flags_json or {},
            "reviewed_by_user_id": record.reviewed_by_user_id,
            "reviewed_at": _iso(record.reviewed_at),
            "raw_row": record.raw_row_json,
            "superseded_by_import_id": record.superseded_by_import_id,
            "superseded_at": _iso(record.superseded_at),
            "lock_version": record.lock_version,
        }
    return result


def serialize_error(
    error: HistoricalImportError, *, is_admin: bool
) -> dict:
    result = {
        "id": error.id,
        "record_id": error.historical_demand_record_id,
        "source_row_number": error.source_row_number,
        "field": error.field_name,
        "code": error.error_code,
        "severity": error.severity,
        "message": error.message,
        "resolution_status": error.resolution_status,
        "created_at": _iso(error.created_at),
    }
    if is_admin:
        result["resolution"] = {
            "action": error.resolution_action,
            "resolved_by_user_id": error.resolved_by_user_id,
            "resolved_at": _iso(error.resolved_at),
        }
    return result


def list_imports(
    *,
    page_value,
    per_page_value,
    status: str | None,
    is_admin: bool,
) -> dict:
    page, per_page = _parse_pagination(page_value, per_page_value)
    query = HistoricalImport.query
    if status:
        normalized = status.strip().casefold()
        if normalized not in HISTORICAL_IMPORT_STATUSES:
            raise ValidationError("El filtro 'status' no es válido.")
        query = query.filter(HistoricalImport.status == normalized)
    pagination = query.order_by(
        HistoricalImport.created_at.desc(), HistoricalImport.id.desc()
    ).paginate(page=page, per_page=per_page, error_out=False)
    return {
        "items": [
            serialize_import(item, is_admin=is_admin) for item in pagination.items
        ],
        "pagination": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        },
    }


def get_import_detail(public_id: str, *, is_admin: bool) -> dict:
    return serialize_import(get_import_or_404(public_id), is_admin=is_admin)


def list_records(
    public_id: str,
    *,
    page_value,
    per_page_value,
    match_status: str | None,
    is_admin: bool,
) -> dict:
    historical_import = get_import_or_404(public_id)
    page, per_page = _parse_pagination(page_value, per_page_value)
    query = HistoricalDemandRecord.query.filter_by(
        historical_import_id=historical_import.id
    )
    if match_status:
        candidate = match_status.strip()
        if candidate not in _MATCH_STATUSES:
            raise ValidationError("El filtro 'match_status' no es válido.")
        query = query.filter(HistoricalDemandRecord.match_status == candidate)
    pagination = query.order_by(
        HistoricalDemandRecord.source_row_number.asc()
    ).paginate(page=page, per_page=per_page, error_out=False)
    return {
        "items": [
            serialize_record(item, is_admin=is_admin) for item in pagination.items
        ],
        "pagination": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        },
    }


def list_relationship_candidates(
    public_id: str,
    record_id: int,
    *,
    page_value,
    per_page_value,
) -> dict:
    """Lista candidatos exactos y vigentes para una relación manual.

    La consulta usa exclusivamente documento y código normalizados. Nunca
    relaciona por nombre y no revela el archivo ni su ruta privada.
    """
    historical_import = get_import_or_404(public_id)
    source_record = HistoricalDemandRecord.query.filter_by(
        id=record_id,
        historical_import_id=historical_import.id,
    ).first()
    if source_record is None:
        raise NotFoundError("La fila histórica solicitada no existe en este lote.")
    page, per_page = _parse_pagination(page_value, per_page_value)

    if (
        source_record.record_type == RECORD_TYPE_SALE
        or not source_record.document_number_normalized
    ):
        return {
            "items": [],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": 0,
                "pages": 0,
            },
        }

    same_import_candidate = and_(
        HistoricalDemandRecord.historical_import_id == historical_import.id,
        HistoricalDemandRecord.source_row_number
        < source_record.source_row_number,
    )
    confirmed_external_candidate = and_(
        HistoricalDemandRecord.historical_import_id != historical_import.id,
        HistoricalImport.status == IMPORT_STATUS_CONFIRMED,
    )
    query = (
        HistoricalDemandRecord.query.join(
            HistoricalImport,
            HistoricalImport.id == HistoricalDemandRecord.historical_import_id,
        )
        .filter(
            HistoricalDemandRecord.id != source_record.id,
            HistoricalDemandRecord.document_number_normalized
            == source_record.document_number_normalized,
            HistoricalDemandRecord.product_code_normalized
            == source_record.product_code_normalized,
            HistoricalDemandRecord.record_type.in_(
                (RECORD_TYPE_SALE, RECORD_TYPE_CORRECTION)
            ),
            HistoricalDemandRecord.effective_status.in_(ACTIVE_RECORD_STATUSES),
            HistoricalDemandRecord.superseded_by_record_id.is_(None),
            or_(same_import_candidate, confirmed_external_candidate),
        )
        .order_by(
            HistoricalDemandRecord.event_date.desc(),
            HistoricalDemandRecord.id.desc(),
        )
    )
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return {
        "items": [
            {
                "id": candidate.id,
                "source_row_number": candidate.source_row_number,
                "record_type": candidate.record_type,
                "event_date": _iso(candidate.event_date),
                "quantity": format(Decimal(candidate.quantity), "f"),
            }
            for candidate in pagination.items
        ],
        "pagination": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        },
    }


def list_errors(
    public_id: str,
    *,
    page_value,
    per_page_value,
    severity: str | None,
    resolution_status: str | None,
    is_admin: bool,
    category: str | None = None,
) -> dict:
    historical_import = get_import_or_404(public_id)
    page, per_page = _parse_pagination(page_value, per_page_value)
    query = HistoricalImportError.query.filter_by(
        historical_import_id=historical_import.id
    )
    if severity:
        candidate = severity.strip().casefold()
        if candidate not in {
            SEVERITY_ERROR,
            SEVERITY_WARNING,
            SEVERITY_REVIEW,
        }:
            raise ValidationError("El filtro 'severity' no es válido.")
        query = query.filter(HistoricalImportError.severity == candidate)
    if resolution_status:
        candidate = resolution_status.strip().casefold()
        if candidate not in {
            RESOLUTION_UNRESOLVED,
            RESOLUTION_RESOLVED,
            RESOLUTION_NOT_REQUIRED,
        }:
            raise ValidationError("El filtro 'resolution_status' no es válido.")
        query = query.filter(
            HistoricalImportError.resolution_status == candidate
        )
    if category:
        candidate = category.strip().casefold()
        if candidate != "duplicate":
            raise ValidationError("El filtro 'category' no es válido.")
        query = query.filter(
            HistoricalImportError.error_code.in_(_DUPLICATE_ERROR_CODES)
        )
    pagination = query.order_by(
        HistoricalImportError.source_row_number.asc(),
        HistoricalImportError.id.asc(),
    ).paginate(page=page, per_page=per_page, error_out=False)
    return {
        "items": [
            serialize_error(item, is_admin=is_admin) for item in pagination.items
        ],
        "pagination": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        },
    }


def _neutralize_csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def errors_csv_stream(public_id: str):
    historical_import = get_import_or_404(public_id)
    filename = f"historical-import-errors-{historical_import.public_id[:8]}.csv"

    def generate():
        yield CSV_BOM
        buffer = io.StringIO(newline="")
        writer = csv.writer(
            buffer,
            delimiter=CSV_DELIMITER,
            quotechar='"',
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\r\n",
        )
        writer.writerow(
            (
                "source_row_number",
                "field",
                "code",
                "severity",
                "message",
                "resolution_status",
            )
        )
        yield buffer.getvalue().encode("utf-8")
        buffer.seek(0)
        buffer.truncate(0)

        query = HistoricalImportError.query.filter_by(
            historical_import_id=historical_import.id
        ).order_by(
            HistoricalImportError.source_row_number.asc(),
            HistoricalImportError.id.asc(),
        )
        for error in query.yield_per(500):
            writer.writerow(
                tuple(
                    _neutralize_csv_cell(value)
                    for value in (
                        error.source_row_number,
                        error.field_name,
                        error.error_code,
                        error.severity,
                        error.message,
                        error.resolution_status,
                    )
                )
            )
            yield buffer.getvalue().encode("utf-8")
            buffer.seek(0)
            buffer.truncate(0)

    return filename, generate()


def template_csv_bytes() -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(
        buffer,
        delimiter=CSV_DELIMITER,
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\r\n",
    )
    writer.writerow(CSV_HEADERS)
    return CSV_BOM + buffer.getvalue().encode("utf-8")
