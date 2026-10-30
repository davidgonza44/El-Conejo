"""Hashes y fingerprints versionados para deduplicación histórica."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import BinaryIO

from app.services.historical_validation_service import decimal_fingerprint_value

FINGERPRINT_VERSION = "fingerprint-v1"
HASH_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class FingerprintResult:
    value: str
    strength: str


def sha256_stream(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = stream.read(HASH_CHUNK_BYTES)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def sha256_file(path: str | Path) -> tuple[str, int]:
    with Path(path).open("rb") as stream:
        return sha256_stream(stream)


def _stable_hash(parts: list[object]) -> str:
    payload = json.dumps(
        parts,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_fingerprint(
    *,
    source_system: str,
    document_type: str,
    document_number_normalized: str | None,
    source_line_id_normalized: str | None,
    event_date: date,
    product_code_normalized: str,
    quantity: Decimal,
    record_type: str,
    version: str = FINGERPRINT_VERSION,
) -> FingerprintResult:
    """Fingerprint sin filename ni número físico de fila."""
    strength = (
        "strong"
        if document_number_normalized and source_line_id_normalized
        else "weak"
    )
    parts: list[object] = [
        version,
        source_system,
        document_type,
        document_number_normalized or "",
        source_line_id_normalized or "",
        event_date.isoformat(),
        product_code_normalized,
        decimal_fingerprint_value(quantity),
        record_type,
    ]
    return FingerprintResult(value=_stable_hash(parts), strength=strength)


def build_possible_duplicate_signature(
    *,
    source_system: str,
    document_type: str,
    event_date: date,
    product_code_normalized: str,
    quantity: Decimal,
    record_type: str,
) -> str:
    """Firma auxiliar débil; jamás se usa como dedupe_key automática."""
    return _stable_hash(
        [
            "possible-duplicate-v1",
            source_system,
            document_type,
            event_date.isoformat(),
            product_code_normalized,
            decimal_fingerprint_value(quantity),
            record_type,
        ]
    )
