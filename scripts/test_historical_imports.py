#!/usr/bin/env python3
"""Suite integral y aislada del importador histórico CSV v1.

Por defecto la suite es *fail-closed*: crea una aplicación Flask propia, una
base SQLite en un directorio ``tempfile``, fixtures efímeras y un servidor
Werkzeug en un puerto local efímero. Nunca reutiliza un servidor existente ni
la URI de base de datos de desarrollo/producción.
"""
from __future__ import annotations

import codecs
import csv
import hashlib
import inspect as python_inspect
import io
import json
import os
import py_compile
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable
from unittest import mock
from uuid import uuid4

import requests
from flask import Flask
from sqlalchemy import BigInteger, MetaData, Table, event, inspect as sa_inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.compiler import compiles
from werkzeug.serving import BaseWSGIServer, make_server

# ``app.config`` llama ``load_dotenv`` al importarse. La suite no debe leer el
# .env real ni siquiera cuando solo se ejecutan helpers puros.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"


@compiles(BigInteger, "sqlite")
def _compile_big_integer_for_sqlite(_type, _compiler, **_kwargs):
    """SQLite solo autoincrementa una PK cuyo tipo compilado sea INTEGER."""
    return "INTEGER"


@event.listens_for(Engine, "connect")
def _configure_isolated_sqlite(dbapi_connection, _connection_record) -> None:
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    Category,
    HistoricalDemandRecord,
    HistoricalImport,
    HistoricalImportError,
    Product,
    User,
)
from app.models.historical_import_error import (  # noqa: E402
    RESOLUTION_NOT_REQUIRED,
    RESOLUTION_UNRESOLVED,
    SEVERITY_ERROR,
    SEVERITY_REVIEW,
    SEVERITY_WARNING,
)
from app.services import historical_import_service, rbac_service  # noqa: E402
from app.services.historical_deduplication_service import (  # noqa: E402
    build_fingerprint,
    sha256_file,
)
from app.services.historical_matching_service import (  # noqa: E402
    build_product_indexes,
    match_product,
)
from app.services.historical_validation_service import (  # noqa: E402
    CSV_HEADERS,
    MAX_CELL_CHARS,
    MAX_COLUMNS,
    MAX_FILE_BYTES,
    MAX_ROWS,
    normalize_code,
    normalize_name,
    resolve_column_mapping,
    validate_historical_row,
)
from app.models.user import ROLE_ADMIN, ROLE_INVENTARIO, ROLE_VENDEDOR  # noqa: E402
from scripts.migrate_historical_imports import (  # noqa: E402
    IMMUTABILITY_TRIGGER_NAMES,
    _check_signature,
    _ensure_immutability_triggers,
    _normalize_sql,
)


RUN_ID = uuid4().hex
RUN_SHORT = RUN_ID[:12].upper()
SOURCE_PREFIX = f"HISTV1_{RUN_SHORT}_"
REQUEST_TIMEOUT = 45

OPERATIONAL_TABLES = (
    "products",
    "stock_movements",
    "delivery_notes",
    "delivery_note_items",
    "categories",
    "users",
)
HISTORICAL_TABLES = (
    "historical_imports",
    "historical_demand_records",
    "historical_import_errors",
)

# Contrato independiente: no se deriva de los modelos y, por tanto, detecta
# que modelo+migración omitan juntos una columna aprobada o usen solo un alias.
EXPECTED_SCHEMA_COLUMNS = {
    "historical_imports": frozenset(
        {
            "id",
            "public_id",
            "original_filename",
            "storage_key",
            "file_sha256",
            "file_size",
            "file_format",
            "delimiter",
            "source_system",
            "period_start",
            "period_end",
            "status",
            "parser_version",
            "validation_version",
            "mapping_version",
            "mapping_json",
            "total_rows",
            "valid_rows",
            "warning_rows",
            "error_rows",
            "pending_match_rows",
            "created_by_user_id",
            "created_at",
            "previewed_at",
            "confirmed_by_user_id",
            "confirmed_at",
            "reverted_by_user_id",
            "reverted_at",
            "revert_reason",
        }
    ),
    "historical_demand_records": frozenset(
        {
            "id",
            "import_id",
            "source_row_number",
            "source_record_id",
            "source_line_id",
            "document_type",
            "document_number",
            "event_date",
            "original_product_code",
            "normalized_product_code",
            "original_product_name",
            "normalized_product_name",
            "product_id",
            "quantity",
            "unit_price",
            "record_type",
            "record_status",
            "related_source_record_id",
            "fingerprint",
            "fingerprint_strength",
            "match_status",
            "include_in_demand",
            "raw_row_json",
            "created_at",
        }
    ),
    "historical_import_errors": frozenset(
        {
            "id",
            "import_id",
            "source_row_number",
            "field_name",
            "error_code",
            "severity",
            "safe_message",
            "redacted_value",
            "resolved",
            "resolved_by_user_id",
            "resolved_at",
            "resolution_note",
            "created_at",
        }
    ),
}
EXPECTED_SCHEMA_FOREIGN_KEYS = {
    "historical_imports": frozenset(
        {
            ("created_by_user_id", "users", "id"),
            ("confirmed_by_user_id", "users", "id"),
            ("reverted_by_user_id", "users", "id"),
        }
    ),
    "historical_demand_records": frozenset(
        {
            ("import_id", "historical_imports", "id"),
            ("product_id", "products", "id"),
        }
    ),
    "historical_import_errors": frozenset(
        {
            ("import_id", "historical_imports", "id"),
            ("resolved_by_user_id", "users", "id"),
        }
    ),
}
EXPECTED_INDEXED_COLUMNS = {
    "historical_imports": frozenset(
        {"public_id", "file_sha256", "status", "created_by_user_id"}
    ),
    "historical_demand_records": frozenset(
        {"import_id", "event_date", "normalized_product_code", "product_id", "fingerprint"}
    ),
    "historical_import_errors": frozenset(
        {"import_id", "severity", "error_code", "resolved_by_user_id"}
    ),
}
API_SNAPSHOT_PATHS = (
    "/api/reports/dashboard-summary",
    "/api/reports/stock-vs-minimum",
    "/api/products",
    "/api/categories",
    "/api/inventory/movements?limit=10000",
    "/api/delivery-notes",
)
FORBIDDEN_ERROR_FRAGMENTS = (
    "traceback",
    "sqlalchemy",
    "pymysql",
    "password_hash",
    "mail_password",
    "google_client_secret",
    "storage_key",
    "db_password",
    str(ROOT).casefold(),
)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    return str(value)


def stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        default=_json_default,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _safe_scalar(value: Any) -> str:
    """Representación breve que nunca imprime cuerpos, tokens ni contraseñas."""
    if value is None:
        return "None"
    if isinstance(value, (bool, int, float)):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return f"{type(value).__name__}(len={len(value)})"
    if isinstance(value, dict):
        return f"dict(keys={sorted(str(key) for key in value)[:12]})"
    text = str(value).replace("\r", " ").replace("\n", " ")
    lowered = text.casefold()
    if any(
        marker in lowered
        for marker in ("password", "secret", "token=", "authorization", "cookie")
    ):
        return "[REDACTADO]"
    return text[:180]


@dataclass
class Defect:
    check_number: int
    test: str
    expected: str
    actual: str
    probable_cause: str
    priority: str


class Runner:
    def __init__(self) -> None:
        self.total = 0
        self.ok = 0
        self.failed = 0
        self.skipped = 0
        self.http_calls = 0
        self.base_seen: set[int] = set()
        self.defects: list[Defect] = []
        self.safe_error_responses: dict[int, Any] = {}
        self.base_url: str | None = None
        self.csrf_tokens: dict[int, str] = {}
        self._lock = threading.Lock()

    def register_csrf(self, session: requests.Session, token: str) -> None:
        self.csrf_tokens[id(session)] = token

    def check(
        self,
        label: str,
        condition: bool,
        *,
        base: int | None = None,
        expected: Any = True,
        actual: Any = None,
        cause: str = "Revisar el contrato y la implementación del módulo histórico.",
        priority: str = "P1",
    ) -> bool:
        with self._lock:
            self.total += 1
            number = self.total
            if base is not None:
                self.base_seen.add(base)
            prefix = f"Base {base} — " if base is not None else ""
            if condition:
                self.ok += 1
                print(f"[OK] #{number:03d} {prefix}{label}")
                return True
            self.failed += 1
            expected_text = _safe_scalar(expected)
            actual_text = _safe_scalar(actual)
            print(
                f"[FALLO] #{number:03d} {prefix}{label} "
                f"(esperado={expected_text}; real={actual_text})"
            )
            self.defects.append(
                Defect(
                    check_number=number,
                    test=f"{prefix}{label}",
                    expected=expected_text,
                    actual=actual_text,
                    probable_cause=cause,
                    priority=priority,
                )
            )
            return False

    def skip(self, label: str, reason: str) -> None:
        with self._lock:
            self.total += 1
            self.skipped += 1
            print(f"[SKIP] #{self.total:03d} {label} ({_safe_scalar(reason)})")

    def request(
        self,
        session: requests.Session | None,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> requests.Response:
        if self.base_url is None:
            raise RuntimeError("El servidor aislado de la suite no está iniciado.")
        csrf_mode = kwargs.pop("csrf_mode", "valid")
        if csrf_mode not in {"valid", "missing", "invalid"}:
            raise ValueError("csrf_mode no reconocido")
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and session:
            token = self.csrf_tokens.get(id(session))
            headers = dict(kwargs.get("headers") or {})
            if csrf_mode == "valid" and token:
                headers.setdefault("X-CSRFToken", token)
            elif csrf_mode == "invalid":
                headers["X-CSRFToken"] = "csrf-invalido-aislado"
            if headers:
                kwargs["headers"] = headers
        with self._lock:
            self.http_calls += 1
        client = session or requests
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        kwargs.setdefault("allow_redirects", False)
        return client.request(method, f"{self.base_url}{path}", **kwargs)

    def remember_error(self, response: Any) -> None:
        status = int(getattr(response, "status_code", 0) or 0)
        if status in {400, 401, 403, 404, 409, 413, 422, 500}:
            self.safe_error_responses.setdefault(status, response)


class ArtifactTracker:
    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix=f"historical_csv_v1_{RUN_ID}_"))
        self.database_path = self.root / "suite.sqlite"
        self.migration_database_path = self.root / "migration-idempotence.sqlite"
        self.instance_path = self.root / "instance"
        self.created_temp_files: list[Path] = []
        self.deleted_temp_files = 0
        self.tracked_storage_keys: set[str] = set()
        self.created_import_ids: set[int] = set()
        self.created_public_ids: set[str] = set()
        self.created_historical_rows = {
            "historical_imports": 0,
            "historical_demand_records": 0,
            "historical_import_errors": 0,
        }
        self.deleted_historical_rows = dict(self.created_historical_rows)
        self.deleted_private_files = 0

    def path(self, tag: str, suffix: str = ".csv") -> Path:
        path = self.root / f"{tag}_{uuid4().hex}{suffix}"
        self.created_temp_files.append(path)
        return path

    def cleanup_temp_files(self) -> None:
        for path in self.created_temp_files:
            if path.exists():
                path.unlink()
                self.deleted_temp_files += 1

    def cleanup_root(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def csv_file(
    artifacts: ArtifactTracker,
    tag: str,
    rows: Iterable[dict[str, Any]],
    *,
    headers: Iterable[str] = CSV_HEADERS,
    delimiter: str = ";",
    bom: bool = True,
) -> Path:
    path = artifacts.path(tag)
    encoding = "utf-8-sig" if bom else "utf-8"
    header_list = list(headers)
    with path.open("w", encoding=encoding, newline="") as target:
        writer = csv.writer(
            target,
            delimiter=delimiter,
            quotechar='"',
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\r\n",
        )
        writer.writerow(header_list)
        for row in rows:
            writer.writerow([row.get(header, "") for header in header_list])
    return path


def raw_file(
    artifacts: ArtifactTracker,
    tag: str,
    content: bytes,
    *,
    suffix: str = ".csv",
) -> Path:
    path = artifacts.path(tag, suffix=suffix)
    path.write_bytes(content)
    return path


def large_ascii_file(artifacts: ArtifactTracker, tag: str, size: int) -> Path:
    path = artifacts.path(tag)
    remaining = size
    with path.open("wb") as target:
        prefix = codecs.BOM_UTF8
        target.write(prefix)
        remaining -= len(prefix)
        chunk = b"A" * (64 * 1024)
        while remaining > 0:
            piece = chunk[: min(len(chunk), remaining)]
            target.write(piece)
            remaining -= len(piece)
    return path


def many_rows_file(
    artifacts: ArtifactTracker,
    tag: str,
    product_code: str,
    row_count: int,
) -> Path:
    path = artifacts.path(tag)
    with path.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.writer(target, delimiter=";", lineterminator="\r\n")
        writer.writerow(CSV_HEADERS)
        row = [
            "2025-01-01",
            product_code,
            "",
            "1",
            "sale",
            "issued",
            "",
            "",
            "",
            "",
        ]
        for _ in range(row_count):
            writer.writerow(row)
    return path


def response_json(response: Any) -> Any:
    if hasattr(response, "get_json") and not isinstance(response, requests.Response):
        return response.get_json(silent=True)
    try:
        return response.json()
    except (ValueError, TypeError):
        return None


def response_content_type(response: Any) -> str:
    if hasattr(response, "headers"):
        return str(response.headers.get("Content-Type", response.headers.get("content-type", "")))
    return ""


def safe_error_json(response: Any, expected_status: int) -> bool:
    status = int(getattr(response, "status_code", 0) or 0)
    payload = response_json(response)
    if (
        status != expected_status
        or "application/json" not in response_content_type(response).casefold()
        or not isinstance(payload, dict)
        or set(payload) != {"error"}
        or not isinstance(payload.get("error"), str)
        or not payload["error"].strip()
    ):
        return False
    serialized = json.dumps(payload, ensure_ascii=False).casefold()
    return not any(fragment in serialized for fragment in FORBIDDEN_ERROR_FRAGMENTS)


def recursive_contains_key(value: Any, forbidden_key: str) -> bool:
    if isinstance(value, dict):
        return forbidden_key in value or any(
            recursive_contains_key(child, forbidden_key) for child in value.values()
        )
    if isinstance(value, list):
        return any(recursive_contains_key(child, forbidden_key) for child in value)
    return False


def inspect_independent_schema(engine) -> tuple[bool, dict[str, Any]]:
    """Compara el esquema real con el contrato aprobado, no con los modelos."""
    inspector = sa_inspect(engine)
    existing = set(inspector.get_table_names())
    details: dict[str, Any] = {"missing_tables": sorted(set(HISTORICAL_TABLES) - existing)}
    ok = not details["missing_tables"]
    for table_name in HISTORICAL_TABLES:
        if table_name not in existing:
            continue
        actual_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        missing_columns = sorted(EXPECTED_SCHEMA_COLUMNS[table_name] - actual_columns)

        indexed_columns: set[str] = set()
        unique_signatures: set[tuple[str, ...]] = set()
        for index in inspector.get_indexes(table_name):
            columns = tuple(index.get("column_names") or ())
            indexed_columns.update(columns)
            if index.get("unique"):
                unique_signatures.add(columns)
        for constraint in inspector.get_unique_constraints(table_name):
            columns = tuple(constraint.get("column_names") or ())
            indexed_columns.update(columns)
            unique_signatures.add(columns)
        primary_key = tuple(
            inspector.get_pk_constraint(table_name).get("constrained_columns") or ()
        )
        indexed_columns.update(primary_key)
        missing_indexes = sorted(
            EXPECTED_INDEXED_COLUMNS[table_name] - indexed_columns
        )

        actual_foreign_keys: set[tuple[str, str, str]] = set()
        for foreign_key in inspector.get_foreign_keys(table_name):
            target = str(foreign_key.get("referred_table") or "")
            for local, remote in zip(
                foreign_key.get("constrained_columns") or (),
                foreign_key.get("referred_columns") or (),
            ):
                actual_foreign_keys.add((str(local), target, str(remote)))
        missing_foreign_keys = sorted(
            EXPECTED_SCHEMA_FOREIGN_KEYS[table_name] - actual_foreign_keys
        )
        sha_unique = True
        if table_name == "historical_imports":
            sha_unique = ("file_sha256",) in unique_signatures

        details[table_name] = {
            "missing_columns": missing_columns,
            "missing_indexes": missing_indexes,
            "missing_foreign_keys": missing_foreign_keys,
            "file_sha256_unique": sha_unique,
        }
        ok = ok and not missing_columns and not missing_indexes
        ok = ok and not missing_foreign_keys and sha_unique
    trigger_names: set[str] = set()
    if engine.url.get_backend_name() == "sqlite":
        with engine.connect() as connection:
            trigger_names = {
                str(row[0])
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
                )
            }
    missing_triggers = sorted(set(IMMUTABILITY_TRIGGER_NAMES.values()) - trigger_names)
    details["missing_immutability_triggers"] = missing_triggers
    ok = ok and not missing_triggers
    return ok, details


def reflected_database_signature(engine, table_names: Iterable[str]) -> str:
    """Firma estable de esquema+conteos para comprobar idempotencia aislada."""
    inspector = sa_inspect(engine)
    metadata = MetaData()
    payload: dict[str, Any] = {}
    with engine.connect() as connection:
        for table_name in sorted(table_names):
            table = Table(table_name, metadata, autoload_with=engine)
            payload[table_name] = {
                "columns": [
                    (
                        column["name"],
                        str(column["type"]),
                        bool(column.get("nullable")),
                    )
                    for column in inspector.get_columns(table_name)
                ],
                "indexes": sorted(
                    (
                        index.get("name"),
                        tuple(index.get("column_names") or ()),
                        bool(index.get("unique")),
                    )
                    for index in inspector.get_indexes(table_name)
                ),
                "unique": sorted(
                    tuple(item.get("column_names") or ())
                    for item in inspector.get_unique_constraints(table_name)
                ),
                "foreign_keys": sorted(
                    (
                        tuple(item.get("constrained_columns") or ()),
                        item.get("referred_table"),
                        tuple(item.get("referred_columns") or ()),
                    )
                    for item in inspector.get_foreign_keys(table_name)
                ),
                "rows": len(connection.execute(select(table)).all()),
            }
    return stable_hash(payload)


class HistoricalImportSuite:
    def __init__(self, runner: Runner, artifacts: ArtifactTracker) -> None:
        self.runner = runner
        self.artifacts = artifacts
        self.artifacts.instance_path.mkdir(parents=True, exist_ok=True)
        database_uri = (
            "sqlite:///"
            + self.artifacts.database_path.resolve().as_posix()
            + "?check_same_thread=false"
        )
        isolated_config = type(
            "HistoricalImportIsolatedConfig",
            (),
            {
                "SECRET_KEY": secrets.token_urlsafe(32),
                "APP_ENV": "testing",
                "TESTING": False,
                "PROPAGATE_EXCEPTIONS": False,
                "SQLALCHEMY_DATABASE_URI": database_uri,
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "SQLALCHEMY_ENGINE_OPTIONS": {
                    "connect_args": {"check_same_thread": False, "timeout": 30}
                },
                "MAIL_ENABLED": False,
                "GOOGLE_CLIENT_ID": None,
                "GOOGLE_CLIENT_SECRET": None,
                "GOOGLE_REDIRECT_URI": None,
            },
        )
        self.app = create_app(isolated_config)
        self.app.instance_path = str(self.artifacts.instance_path.resolve())
        self.server: BaseWSGIServer | None = None
        self.server_thread: threading.Thread | None = None
        self.source_counter = 0
        self.admin: requests.Session | None = None
        self.inventory: requests.Session | None = None
        self.seller: requests.Session | None = None
        self.credentials: dict[str, tuple[str, str]] = {}
        self.active_product: dict[str, Any] | None = None
        self.inactive_product: dict[str, Any] | None = None
        self.admin_user_id: int | None = None
        self.initial_operational: dict[str, Any] = {}
        self.initial_historical: dict[str, Any] = {}
        self.initial_private: dict[str, Any] = {}
        self.initial_api: dict[str, Any] = {}
        self.initial_historical_ids: set[int] = set()
        self.initial_private_names: set[str] = set()
        self.valid_import: dict[str, Any] | None = None
        self.valid_path: Path | None = None
        self.valid_original_filename: str | None = None

    @contextmanager
    def context(self):
        with self.app.app_context():
            db.session.remove()
            try:
                yield
            finally:
                db.session.remove()

    def source(self, tag: str) -> str:
        self.source_counter += 1
        clean_tag = "".join(char if char.isalnum() else "_" for char in tag.upper())
        return f"{SOURCE_PREFIX}{self.source_counter:03d}_{clean_tag}"[:100]

    def assert_isolated_runtime(self) -> None:
        with self.context():
            url = db.engine.url
            database = Path(str(url.database or "")).resolve()
        root = self.artifacts.root.resolve()
        instance_path = Path(self.app.instance_path).resolve()
        if url.get_backend_name() != "sqlite":
            raise RuntimeError("La suite solo puede ejecutarse con SQLite aislado.")
        if root not in database.parents or database != self.artifacts.database_path.resolve():
            raise RuntimeError("La base de la suite debe vivir dentro de su tempfile.")
        if root not in instance_path.parents:
            raise RuntimeError("El instance_path de la suite debe vivir dentro de tempfile.")

    def initialize_isolated_database(self) -> None:
        self.assert_isolated_runtime()
        suffix = RUN_ID[:10].lower()
        self.credentials = {
            "admin": (f"hist_admin_{suffix}", secrets.token_urlsafe(24)),
            "inventario": (f"hist_inventory_{suffix}", secrets.token_urlsafe(24)),
            "vendedor": (f"hist_seller_{suffix}", secrets.token_urlsafe(24)),
        }
        with self.context():
            db.create_all()
            # La autorización usa las tablas RBAC persistidas, también en esta
            # SQLite temporal; una tabla vacía no concede los permisos del rol.
            rbac_service.ensure_seeded()
            with db.engine.begin() as connection:
                _ensure_immutability_triggers(connection)
            category = Category(
                name=f"Categoría histórica aislada {RUN_SHORT}",
                description="Fixture efímera de la suite histórica.",
            )
            db.session.add(category)
            db.session.flush()
            active = Product(
                code=f"HIST-ACT-{RUN_SHORT}",
                name=f"Producto activo aislado {RUN_SHORT}",
                category_id=category.id,
                unit="unidad",
                current_stock=37,
                minimum_stock=9,
                purchase_price=Decimal("1.00"),
                sale_price=Decimal("2.00"),
                is_active=True,
            )
            inactive = Product(
                code=f"HIST-INA-{RUN_SHORT}",
                name=f"Producto inactivo aislado {RUN_SHORT}",
                category_id=category.id,
                unit="unidad",
                current_stock=4,
                minimum_stock=2,
                purchase_price=Decimal("1.00"),
                sale_price=Decimal("2.00"),
                is_active=False,
            )
            db.session.add_all([active, inactive])
            for role, display in (
                (ROLE_ADMIN, "Administrador"),
                (ROLE_INVENTARIO, "Inventario"),
                (ROLE_VENDEDOR, "Vendedor"),
            ):
                username, password = self.credentials[role]
                user = User(
                    name=f"{display} aislado {RUN_SHORT}",
                    email=f"{username}@example.test",
                    username=username,
                    role=role,
                    is_active=True,
                    email_verified=True,
                )
                user.set_password(password)
                db.session.add(user)
            db.session.commit()

    def ensure_server(self) -> None:
        if self.server is not None:
            raise RuntimeError("El servidor aislado ya fue iniciado.")
        # Servidor de una hebra: dos clientes pueden competir, pero SQLite no
        # recibe escrituras simultáneas que producirían falsos "database locked".
        self.server = make_server("127.0.0.1", 0, self.app, threaded=False)
        self.runner.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.server_thread = threading.Thread(
            target=self.server.serve_forever,
            name=f"historical-suite-{RUN_SHORT}",
            daemon=True,
        )
        self.server_thread.start()
        try:
            response = requests.get(
                f"{self.runner.base_url}/api/historical-imports",
                timeout=4,
                allow_redirects=False,
            )
            running = response.status_code == 401
        except requests.RequestException:
            running = False
        self.runner.check(
            "Servidor Werkzeug aislado iniciado en puerto efímero",
            running,
            expected="servidor propio con HTTP 401",
            actual=(response.status_code if running else "sin respuesta"),
            cause="El servidor WSGI aislado no pudo iniciar sobre loopback.",
            priority="P0",
        )
        if not running:
            raise RuntimeError("El servidor Werkzeug aislado no está disponible")

    def stop_server(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.server_thread is not None:
            self.server_thread.join(timeout=10)
            if self.server_thread.is_alive():
                raise RuntimeError("El servidor Werkzeug aislado no se detuvo.")
        self.server = None
        self.server_thread = None
        self.runner.base_url = None

    def dispose_database(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def table_snapshot(self, table_name: str) -> dict[str, Any]:
        metadata = MetaData()
        table = Table(table_name, metadata, autoload_with=db.engine)
        primary_keys = list(table.primary_key.columns)
        statement = select(table)
        if primary_keys:
            statement = statement.order_by(*primary_keys)
        rows = [dict(row) for row in db.session.execute(statement).mappings().all()]
        if table_name == "users":
            # Los logins de la propia suite actualizan este campo de auditoría.
            for row in rows:
                row.pop("last_login_at", None)
                row.pop("updated_at", None)
        ids = [
            tuple(row[column.name] for column in primary_keys)
            for row in rows
        ]
        result: dict[str, Any] = {
            "count": len(rows),
            "ids_hash": stable_hash(ids),
            "rows_hash": stable_hash(rows),
        }
        if table_name == "products":
            stock_values = [
                {
                    "id": row["id"],
                    "current_stock": row["current_stock"],
                    "minimum_stock": row["minimum_stock"],
                    "is_active": bool(row["is_active"]),
                }
                for row in rows
            ]
            result.update(
                {
                    "stock_values_hash": stable_hash(stock_values),
                    "current_stock_sum": sum(
                        int(row["current_stock"]) for row in rows
                    ),
                    "minimum_stock_sum": sum(
                        int(row["minimum_stock"]) for row in rows
                    ),
                    "active_count": sum(bool(row["is_active"]) for row in rows),
                }
            )
        return result

    def db_snapshot(self, tables: Iterable[str]) -> dict[str, Any]:
        with self.context():
            return {name: self.table_snapshot(name) for name in tables}

    def private_snapshot(self) -> dict[str, Any]:
        root = Path(self.app.instance_path) / historical_import_service.PRIVATE_STORAGE_DIR
        files: dict[str, Any] = {}
        if root.exists():
            for path in sorted(root.iterdir(), key=lambda item: item.name):
                if path.is_file():
                    digest, size = sha256_file(path)
                    files[path.name] = {"sha256": digest, "size": size}
        return {
            "count": len(files),
            "files_hash": stable_hash(files),
            "files": files,
        }

    def api_snapshot(self, session: requests.Session) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for path in API_SNAPSHOT_PATHS:
            response = self.runner.request(session, "GET", path)
            payload = response_json(response)
            result[path] = {
                "status": response.status_code,
                "content_type": response.headers.get("Content-Type", ""),
                "payload_hash": stable_hash(payload),
            }
        return result

    def print_snapshot(self, title: str, snapshot: dict[str, Any]) -> None:
        print(f"\n--- {title} ---")
        for name, values in snapshot.items():
            if not isinstance(values, dict):
                continue
            extras = ""
            if name == "products":
                extras = (
                    f" stock_sum={values.get('current_stock_sum')}"
                    f" minimum_sum={values.get('minimum_stock_sum')}"
                    f" activos={values.get('active_count')}"
                    f" stock_hash={values.get('stock_values_hash')}"
                )
            print(
                f"{name}: count={values.get('count', '-')}"
                f" ids_hash={values.get('ids_hash', '-')}"
                f" rows_hash={values.get('rows_hash', '-')}{extras}"
            )

    def inspect_prerequisites(self) -> None:
        with self.context():
            active = Product.query.filter_by(is_active=True).order_by(Product.id).first()
            inactive = Product.query.filter_by(is_active=False).order_by(Product.id).first()
            admin = User.query.filter_by(role="admin", is_active=True).order_by(User.id).first()
            if active:
                self.active_product = {
                    "id": active.id,
                    "code": active.code,
                    "name": active.name,
                    "is_active": bool(active.is_active),
                }
            if inactive:
                self.inactive_product = {
                    "id": inactive.id,
                    "code": inactive.code,
                    "name": inactive.name,
                    "is_active": bool(inactive.is_active),
                }
            self.admin_user_id = admin.id if admin else None
            self.initial_historical_ids = {
                value
                for (value,) in db.session.query(HistoricalImport.id).all()
            }
        self.runner.check(
            "Existe fixture efímera de producto activo",
            self.active_product is not None,
            expected="al menos un producto activo",
            actual=self.active_product is not None,
            cause="La inicialización aislada no creó el producto activo.",
            priority="P0",
        )
        self.runner.check(
            "Existe fixture efímera de producto inactivo",
            self.inactive_product is not None,
            expected="un producto inactivo aislado",
            actual=self.inactive_product is not None,
            cause="La inicialización aislada no creó el producto inactivo.",
            priority="P0",
        )
        self.runner.check(
            "Existe fixture efímera de administrador activo",
            self.admin_user_id is not None,
            expected="usuario admin activo",
            actual=self.admin_user_id is not None,
            cause="La inicialización aislada no creó el administrador.",
            priority="P0",
        )

    def login(self, identifier: str, password: str, role_label: str) -> requests.Session:
        session = requests.Session()
        response = self.runner.request(
            session,
            "POST",
            "/api/auth/login",
            json={"identifier": identifier, "password": password},
        )
        payload = response_json(response)
        role = (
            payload.get("user", {}).get("role")
            if isinstance(payload, dict)
            else None
        )
        csrf_ready = role == ROLE_VENDEDOR
        if response.status_code == 200 and role in {ROLE_ADMIN, ROLE_INVENTARIO}:
            page = self.runner.request(session, "GET", "/historical-imports")
            match = re.search(r'csrfToken:\s*(".*?")\s*,', page.text)
            if page.status_code == 200 and match:
                token = json.loads(match.group(1))
                if isinstance(token, str) and token:
                    self.runner.register_csrf(session, token)
                    csrf_ready = True
        self.runner.check(
            f"Login fixture {role_label} y CSRF de sesión sin exponer credenciales",
            response.status_code == 200
            and isinstance(payload, dict)
            and isinstance(payload.get("user"), dict)
            and role is not None
            and csrf_ready
            and "password_hash" not in json.dumps(payload, ensure_ascii=False),
            expected="HTTP 200, payload público y CSRF para roles autorizados",
            actual=response.status_code,
            cause="Las fixtures aisladas o el token CSRF de la página no están disponibles.",
            priority="P0",
        )
        return session

    def row(
        self,
        *,
        code: str | None = None,
        name: str | None = None,
        event_date: str = "2025-06-15",
        quantity: str = "1",
        record_type: str = "sale",
        record_status: str = "issued",
        document_number: str = "",
        source_record_id: str = "",
        source_line_id: str = "",
        unit_price: str = "",
    ) -> dict[str, str]:
        assert self.active_product is not None
        return {
            "event_date": event_date,
            "product_code": self.active_product["code"] if code is None else code,
            "product_name": self.active_product["name"] if name is None else name,
            "quantity": quantity,
            "record_type": record_type,
            "record_status": record_status,
            "document_number": document_number,
            "source_record_id": source_record_id,
            "source_line_id": source_line_id,
            "unit_price": unit_price,
        }

    def detect_private_additions(self) -> None:
        """No hace descubrimiento global: solo se limpian claves de lotes propios."""
        return

    def track_import(self, public_id: str) -> dict[str, Any]:
        with self.context():
            item = HistoricalImport.query.filter_by(public_id=public_id).first()
            if item is None:
                raise RuntimeError("El upload respondió un lote no persistido")
            if not item.source_system.startswith(SOURCE_PREFIX):
                raise RuntimeError("El lote no pertenece a esta ejecución aislada")
            self.artifacts.created_import_ids.add(int(item.id))
            self.artifacts.created_public_ids.add(item.public_id)
            self.artifacts.tracked_storage_keys.add(item.storage_key)
            return {
                "id": int(item.id),
                "public_id": item.public_id,
                "storage_key": item.storage_key,
                "sha256": item.sha256,
                "original_filename": item.original_filename,
                "source_system": item.source_system,
                "status": item.status,
            }

    def upload_path(
        self,
        session: requests.Session,
        path: Path,
        *,
        tag: str,
        source_system: str | None = None,
        client_filename: str | None = None,
        timeout: int = REQUEST_TIMEOUT,
    ) -> tuple[requests.Response, Any, dict[str, Any] | None]:
        source = source_system or self.source(tag)
        with path.open("rb") as stream:
            response = self.runner.request(
                session,
                "POST",
                "/api/historical-imports/upload",
                files={
                    "file": (
                        client_filename or path.name,
                        stream,
                        "text/csv",
                    )
                },
                data={
                    "source_system": source,
                    "document_type": "historical_demand",
                },
                timeout=timeout,
            )
        payload = response_json(response)
        tracked = None
        if response.status_code == 201 and isinstance(payload, dict):
            public_id = (payload.get("historical_import") or {}).get("id")
            if isinstance(public_id, str):
                tracked = self.track_import(public_id)
        if response.status_code >= 400:
            self.runner.remember_error(response)
        return response, payload, tracked

    def import_state(self, public_id: str) -> dict[str, Any] | None:
        with self.context():
            item = HistoricalImport.query.filter_by(public_id=public_id).first()
            if item is None:
                return None
            return {
                "id": int(item.id),
                "public_id": item.public_id,
                "status": item.status,
                "storage_key": item.storage_key,
                "sha256": item.sha256,
                "file_size_bytes": int(item.file_size_bytes),
                "source_system": item.source_system,
                "created_by_user_id": item.created_by_user_id,
                "previewed_by_user_id": item.previewed_by_user_id,
                "dry_run_by_user_id": item.dry_run_by_user_id,
                "confirmed_by_user_id": item.confirmed_by_user_id,
                "reverted_by_user_id": item.reverted_by_user_id,
                "created_at": item.created_at,
                "previewed_at": item.previewed_at,
                "dry_run_at": item.dry_run_at,
                "confirmed_at": item.confirmed_at,
                "reverted_at": item.reverted_at,
                "reversal_reason": item.reversal_reason,
                "confirmation_token_hash": item.confirmation_token_hash,
                "confirmation_token_expires_at": item.confirmation_token_expires_at,
                "confirmation_token_used_at": item.confirmation_token_used_at,
                "lock_version": item.lock_version,
                "mapping": item.column_mapping_json,
                "metadata": item.metadata_json,
                "counts": {
                    "total": item.total_rows,
                    "valid": item.valid_rows,
                    "errors": item.error_count,
                    "warnings": item.warning_count,
                    "reviews": item.review_count,
                    "matched": item.matched_count,
                    "strong": item.strong_fingerprint_count,
                    "weak": item.weak_fingerprint_count,
                },
            }

    def records(self, public_id: str) -> list[dict[str, Any]]:
        with self.context():
            item = HistoricalImport.query.filter_by(public_id=public_id).first()
            if item is None:
                return []
            records = (
                HistoricalDemandRecord.query.filter_by(
                    historical_import_id=item.id
                )
                .order_by(HistoricalDemandRecord.source_row_number)
                .all()
            )
            return [
                {
                    attribute.key: getattr(record, attribute.key)
                    for attribute in sa_inspect(
                        HistoricalDemandRecord
                    ).column_attrs
                }
                for record in records
            ]

    def errors(self, public_id: str) -> list[dict[str, Any]]:
        with self.context():
            item = HistoricalImport.query.filter_by(public_id=public_id).first()
            if item is None:
                return []
            errors = (
                HistoricalImportError.query.filter_by(
                    historical_import_id=item.id
                )
                .order_by(
                    HistoricalImportError.source_row_number,
                    HistoricalImportError.id,
                )
                .all()
            )
            return [
                {
                    attribute.key: getattr(error, attribute.key)
                    for attribute in sa_inspect(
                        HistoricalImportError
                    ).column_attrs
                }
                for error in errors
            ]

    @staticmethod
    def canonical_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ignored = {
            "id",
            "historical_import_id",
            "created_at",
            "updated_at",
            "lock_version",
        }
        return [
            {key: value for key, value in row.items() if key not in ignored}
            for row in rows
        ]

    def private_path(self, storage_key: str) -> Path:
        return (
            Path(self.app.instance_path)
            / historical_import_service.PRIVATE_STORAGE_DIR
            / storage_key
        )

    def direct_insert_issue(
        self,
        public_id: str,
        *,
        code: str,
        severity: str,
        message: str,
        field: str | None = None,
        resolution_status: str = RESOLUTION_UNRESOLVED,
    ) -> int:
        with self.context():
            item = HistoricalImport.query.filter_by(public_id=public_id).one()
            issue = HistoricalImportError(
                historical_import_id=item.id,
                source_row_number=2,
                field_name=field,
                error_code=code,
                severity=severity,
                message=message,
                resolution_status=resolution_status,
            )
            db.session.add(issue)
            db.session.commit()
            return int(issue.id)

    def delete_issues(self, issue_ids: Iterable[int]) -> None:
        ids = list(issue_ids)
        if not ids:
            return
        with self.context():
            HistoricalImportError.query.filter(
                HistoricalImportError.id.in_(ids)
            ).delete(synchronize_session=False)
            db.session.commit()

    def run_migration_idempotence(self) -> None:
        migration_instance = self.artifacts.root / "migration-instance"
        migration_instance.mkdir(parents=True, exist_ok=True)
        migration_uri = (
            "sqlite:///"
            + self.artifacts.migration_database_path.resolve().as_posix()
            + "?check_same_thread=false"
        )
        migration_app = Flask(
            f"historical_migration_{RUN_ID}",
            instance_path=str(migration_instance.resolve()),
        )
        migration_app.config.update(
            SECRET_KEY=secrets.token_urlsafe(32),
            TESTING=True,
            SQLALCHEMY_DATABASE_URI=migration_uri,
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SQLALCHEMY_ENGINE_OPTIONS={
                "connect_args": {"check_same_thread": False, "timeout": 30}
            },
        )
        db.init_app(migration_app)
        schema_ok = False
        idempotent = False
        operational_unchanged = False
        schema_details: dict[str, Any] = {}
        migration_engine = None
        with migration_app.app_context():
            migration_engine = db.engine
            migration_database = Path(str(migration_engine.url.database or "")).resolve()
            if (
                migration_engine.url.get_backend_name() != "sqlite"
                or migration_database
                != self.artifacts.migration_database_path.resolve()
                or self.artifacts.root.resolve() not in migration_database.parents
            ):
                raise RuntimeError("La migración de prueba no usa SQLite temporal.")

            # Se crea solo el soporte operativo vacío y luego se reconstruyen
            # las tres tablas históricas con checkfirst, como migración manual.
            db.create_all()
            for model in (
                HistoricalImportError,
                HistoricalDemandRecord,
                HistoricalImport,
            ):
                model.__table__.drop(bind=migration_engine, checkfirst=True)
            operational_names = sorted(
                set(sa_inspect(migration_engine).get_table_names())
                - set(HISTORICAL_TABLES)
            )
            operational_before = reflected_database_signature(
                migration_engine, operational_names
            )
            for model in (
                HistoricalImport,
                HistoricalDemandRecord,
                HistoricalImportError,
            ):
                model.__table__.create(bind=migration_engine, checkfirst=True)
            with migration_engine.begin() as connection:
                _ensure_immutability_triggers(connection)
            first_signature = reflected_database_signature(
                migration_engine, HISTORICAL_TABLES
            )
            schema_ok, schema_details = inspect_independent_schema(migration_engine)

            for model in (
                HistoricalImport,
                HistoricalDemandRecord,
                HistoricalImportError,
            ):
                model.__table__.create(bind=migration_engine, checkfirst=True)
            with migration_engine.begin() as connection:
                _ensure_immutability_triggers(connection)
            second_signature = reflected_database_signature(
                migration_engine, HISTORICAL_TABLES
            )
            operational_after = reflected_database_signature(
                migration_engine, operational_names
            )
            idempotent = first_signature == second_signature
            operational_unchanged = operational_before == operational_after
            db.session.remove()

        assert migration_engine is not None
        migration_engine.dispose()
        self.runner.check(
            "Migración aislada cumple el esquema contractual independiente",
            schema_ok,
            expected="columnas, FKs e índices contractuales exactos presentes",
            actual=schema_details,
            cause="Los modelos/migración usan alias o carecen de elementos aprobados.",
            priority="P0",
        )
        self.runner.check(
            "Migración SQLite temporal es idempotente y no cambia operativa",
            idempotent and operational_unchanged,
            expected="snapshots idénticos",
            actual=(idempotent, operational_unchanged),
            cause="La segunda creación checkfirst alteró esquema/datos aislados.",
            priority="P0",
        )

    def static_contract_checks(self) -> None:
        script_path = ROOT / "app" / "static" / "js" / "historical_imports.js"
        template_path = ROOT / "app" / "templates" / "historical_imports.html"
        script = script_path.read_text(encoding="utf-8")
        template = template_path.read_text(encoding="utf-8")
        exact_message = "Esta importación no modifica el inventario actual."

        self.runner.check(
            "Frontend estático acepta solo CSV y rechaza XLSX",
            'accept=".csv,text/csv"' in template
            and "XLSX no está permitido" in script
            and ".xlsx" not in template.casefold(),
            expected="accept CSV y rechazo XLSX",
            actual="contrato estático inspeccionado",
            cause="historical_imports.html/js no limita el selector a CSV.",
            priority="P1",
        )
        self.runner.check(
            "Frontend muestra el mensaje exacto de inventario inalterado",
            exact_message in template,
            expected=exact_message,
            actual=exact_message in template,
            cause="historical_imports.html perdió el aviso de seguridad requerido.",
            priority="P1",
        )
        lowered = script.casefold()
        self.runner.check(
            "Frontend no usa innerHTML, Web Storage, ruta privada ni predicción",
            "innerhtml" not in lowered
            and "localstorage" not in lowered
            and "sessionstorage" not in lowered
            and "/instance/" not in lowered
            and "storage_key" not in lowered
            and "predic" not in lowered,
            expected="ningún patrón prohibido",
            actual="análisis estático",
            cause="historical_imports.js incorporó un sink HTML, persistencia cliente o alcance no autorizado.",
            priority="P1",
        )

        node = shutil.which("node")
        if node:
            result = subprocess.run(
                [node, "--check", str(script_path)],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
                check=False,
            )
            self.runner.check(
                "node --check valida historical_imports.js",
                result.returncode == 0,
                expected=0,
                actual=result.returncode,
                cause="El JavaScript del importador contiene un error de sintaxis.",
                priority="P1",
            )
        else:
            self.runner.skip("node --check valida historical_imports.js", "node no instalado")

        try:
            with self.app.app_context():
                self.app.jinja_env.get_template("historical_imports.html")
            jinja_ok = True
        except Exception:
            jinja_ok = False
        self.runner.check(
            "Jinja compila historical_imports.html",
            jinja_ok,
            expected="template compilable",
            actual=jinja_ok,
            cause="El template histórico contiene sintaxis Jinja inválida.",
            priority="P1",
        )

        pyc = self.artifacts.path("py_compile_self", suffix=".pyc")
        try:
            py_compile.compile(str(Path(__file__)), cfile=str(pyc), doraise=True)
            compile_ok = True
        except py_compile.PyCompileError:
            compile_ok = False
        self.runner.check(
            "py_compile valida la suite nueva",
            compile_ok,
            expected="suite compilable",
            actual=compile_ok,
            cause="scripts/test_historical_imports.py contiene sintaxis Python inválida.",
            priority="P0",
        )

    def access_checks_before_data(self) -> None:
        response = self.runner.request(None, "GET", "/api/historical-imports")
        self.runner.remember_error(response)
        self.runner.check(
            "API sin sesión responde 401 JSON sin redirect",
            safe_error_json(response, 401) and "Location" not in response.headers,
            base=1,
            expected="401 JSON",
            actual=response.status_code,
            cause="El decorador de permisos o el unauthorized_handler no preserva JSON.",
            priority="P0",
        )
        page = self.runner.request(None, "GET", "/historical-imports")
        self.runner.check(
            "Página sin sesión redirige a login",
            page.status_code == 302
            and "/login" in (page.headers.get("Location") or ""),
            base=1,
            expected="302 /login",
            actual=f"{page.status_code} {page.headers.get('Location', '')}",
            cause="pages.historical_imports no aplica login_required.",
            priority="P0",
        )

    def csrf_contract_checks(self) -> None:
        assert self.admin
        endpoint = "/api/historical-imports/upload"
        missing = self.runner.request(
            self.admin,
            "POST",
            endpoint,
            csrf_mode="missing",
        )
        invalid = self.runner.request(
            self.admin,
            "POST",
            endpoint,
            csrf_mode="invalid",
        )
        valid = self.runner.request(
            self.admin,
            "POST",
            endpoint,
            csrf_mode="valid",
        )
        for response in (missing, invalid, valid):
            self.runner.remember_error(response)
        missing_payload = response_json(missing) or {}
        invalid_payload = response_json(invalid) or {}
        valid_payload = response_json(valid) or {}
        self.runner.check(
            "CSRF ausente bloquea POST histórico antes del controlador",
            missing.status_code == 400
            and safe_error_json(missing, 400)
            and "csrf" in str(missing_payload.get("error", "")).casefold(),
            expected="400 JSON CSRF",
            actual=missing.status_code,
            cause="La ruta mutante no exige token CSRF para sesión cookie.",
            priority="P0",
        )
        self.runner.check(
            "CSRF inválido bloquea POST histórico",
            invalid.status_code == 400
            and safe_error_json(invalid, 400)
            and "csrf" in str(invalid_payload.get("error", "")).casefold(),
            expected="400 JSON CSRF",
            actual=invalid.status_code,
            cause="La ruta mutante acepta un token CSRF no ligado a la sesión.",
            priority="P0",
        )
        self.runner.check(
            "CSRF válido supera la capa CSRF y llega a validación de upload",
            valid.status_code == 400
            and safe_error_json(valid, 400)
            and "csrf" not in str(valid_payload.get("error", "")).casefold()
            and "file" in str(valid_payload.get("error", "")).casefold(),
            expected="400 del campo file, no de CSRF",
            actual=valid_payload,
            cause="El token emitido por la página no valida para la misma sesión.",
            priority="P0",
        )

    def page_role_checks(self) -> None:
        assert self.admin and self.inventory and self.seller
        exact_message = "Esta importación no modifica el inventario actual."
        admin_page = self.runner.request(self.admin, "GET", "/historical-imports")
        inventory_page = self.runner.request(
            self.inventory, "GET", "/historical-imports"
        )
        seller_page = self.runner.request(
            self.seller, "GET", "/historical-imports"
        )
        seller_products = self.runner.request(self.seller, "GET", "/products")
        self.runner.check(
            "Roles de página y sidebar: admin/inventario sí, vendedor no",
            admin_page.status_code == 200
            and inventory_page.status_code == 200
            and exact_message in admin_page.text
            and "Importación histórica" in admin_page.text
            and "Importación histórica" in inventory_page.text
            and seller_page.status_code == 403
            and "No tiene permisos para acceder a esta sección." in seller_page.text
            and "Importación histórica" not in seller_products.text,
            base=2,
            expected="admin/inventario 200; vendedor 403 y sin sidebar",
            actual=(
                admin_page.status_code,
                inventory_page.status_code,
                seller_page.status_code,
            ),
            cause="pages.py/base_app.html no aplican la matriz de roles histórica.",
            priority="P0",
        )

    def template_check(self) -> None:
        assert self.admin
        response = self.runner.request(
            self.admin, "GET", "/api/historical-imports/template.csv"
        )
        expected = codecs.BOM_UTF8 + (
            ";".join(CSV_HEADERS) + "\r\n"
        ).encode("utf-8")
        self.runner.check(
            "Plantilla CSV es exacta, vacía y sin datos ficticios",
            response.status_code == 200
            and response.content == expected
            and response.headers.get("X-Historical-CSV-Schema")
            == "historical-csv-v1"
            and "text/csv" in response.headers.get("Content-Type", ""),
            expected=f"{len(expected)} bytes de header canónico",
            actual=f"HTTP {response.status_code}, {len(response.content)} bytes",
            cause="template_csv_bytes/controller no devuelve solo el header v1 con BOM.",
            priority="P1",
        )

    def invalid_upload_checks(self) -> dict[str, Any]:
        assert self.admin and self.active_product
        outcomes: dict[str, Any] = {}

        xlsx = raw_file(
            self.artifacts,
            "xlsx_rejected",
            b"PK\x03\x04not-an-xlsx",
            suffix=".xlsx",
        )
        response, _, _ = self.upload_path(
            self.admin, xlsx, tag="xlsx", client_filename="history.xlsx"
        )
        outcomes["400"] = response
        self.runner.check(
            "XLSX es rechazado como no soportado",
            response.status_code == 400 and safe_error_json(response, 400),
            base=4,
            expected="HTTP 400 JSON",
            actual=response.status_code,
            cause="_safe_original_filename no limita la extensión a .csv.",
            priority="P0",
        )

        invalid_extension = raw_file(
            self.artifacts,
            "invalid_extension",
            codecs.BOM_UTF8 + b"a;b\r\n",
            suffix=".txt",
        )
        response, _, _ = self.upload_path(
            self.admin,
            invalid_extension,
            tag="invalid_extension",
            client_filename="history.txt",
        )
        self.runner.check(
            "Extensión distinta de CSV es rechazada",
            response.status_code == 400,
            base=5,
            expected=400,
            actual=response.status_code,
            cause="_safe_original_filename permite extensiones no autorizadas.",
            priority="P0",
        )

        over_10 = large_ascii_file(
            self.artifacts, "over_10_mib", MAX_FILE_BYTES + 1
        )
        response_10, _, _ = self.upload_path(
            self.admin, over_10, tag="over_10_mib", timeout=120
        )
        outcomes["413"] = response_10
        over_12 = large_ascii_file(
            self.artifacts, "over_12_mib", 12 * 1024 * 1024 + 1
        )
        response_12, _, _ = self.upload_path(
            self.admin, over_12, tag="over_12_mib", timeout=120
        )
        self.runner.check(
            "Límites 10 MiB de archivo y 12 MiB multipart devuelven 413",
            response_10.status_code == 413
            and response_12.status_code == 413
            and safe_error_json(response_10, 413)
            and safe_error_json(response_12, 413),
            base=6,
            expected=(413, 413),
            actual=(response_10.status_code, response_12.status_code),
            cause="MAX_FILE_BYTES/MAX_CONTENT_LENGTH no se aplican en ambas capas.",
            priority="P0",
        )

        empty = raw_file(self.artifacts, "empty", b"")
        response, _, _ = self.upload_path(self.admin, empty, tag="empty")
        outcomes["422"] = response
        self.runner.check(
            "CSV vacío devuelve 422 seguro",
            response.status_code == 422 and safe_error_json(response, 422),
            base=7,
            expected=422,
            actual=response.status_code,
            cause="_copy_upload_to_private no detecta el stream vacío.",
            priority="P0",
        )

        no_bom = csv_file(
            self.artifacts,
            "without_bom",
            [
                self.row(
                    document_number=f"DOC-{RUN_SHORT}-NOBOM",
                    source_line_id="1",
                )
            ],
            bom=False,
        )
        response, _, _ = self.upload_path(self.admin, no_bom, tag="without_bom")
        self.runner.check(
            "UTF-8 sin BOM es rechazado y UTF-8 BOM es obligatorio",
            response.status_code == 422,
            expected=422,
            actual=response.status_code,
            cause="_copy_upload_to_private no exige UTF-8-sig/BOM.",
            priority="P0",
        )

        wrong_delimiter = csv_file(
            self.artifacts,
            "wrong_delimiter",
            [
                self.row(
                    document_number=f"DOC-{RUN_SHORT}-COMMA",
                    source_line_id="1",
                )
            ],
            delimiter=",",
        )
        response, _, wrong_import = self.upload_path(
            self.admin, wrong_delimiter, tag="wrong_delimiter"
        )
        preview = None
        if wrong_import:
            preview = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{wrong_import['public_id']}/preview",
                json={},
            )
            self.runner.remember_error(preview)
        self.runner.check(
            "Delimitador incorrecto se rechaza antes de crear el lote",
            response.status_code == 422
            and wrong_import is None
            and preview is None,
            expected="upload estructural 422 sin lote",
            actual=(
                response.status_code,
                preview.status_code if preview is not None else None,
            ),
            cause="_inspect_csv_structure no detecta que el CSV no usa punto y coma.",
            priority="P1",
        )

        missing_headers = csv_file(
            self.artifacts,
            "missing_headers",
            [{"event_date": "2025-01-01", "product_code": self.active_product["code"]}],
            headers=("event_date", "product_code"),
        )
        response, _, missing_import = self.upload_path(
            self.admin, missing_headers, tag="missing_headers"
        )
        missing_preview = None
        if missing_import:
            missing_preview = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{missing_import['public_id']}/preview",
                json={},
            )
            self.runner.remember_error(missing_preview)
        self.runner.check(
            "Headers obligatorios faltantes bloquean carga o preview",
            response.status_code == 422
            and missing_import is None
            and missing_preview is None,
            base=8,
            expected="upload estructural 422 sin lote",
            actual=response.status_code,
            cause="La validación estructural no exige las columnas mínimas.",
            priority="P0",
        )

        nul_content = (
            codecs.BOM_UTF8
            + (";".join(CSV_HEADERS) + "\r\n").encode("utf-8")
            + b"2025-01-01;ABC\x00DEF;;1;sale;issued;;;;\r\n"
        )
        nul_file = raw_file(self.artifacts, "nul_control", nul_content)
        response, _, _ = self.upload_path(
            self.admin, nul_file, tag="nul_control"
        )
        self.runner.check(
            "Caracteres NUL/controles se rechazan durante upload",
            response.status_code == 422,
            expected=422,
            actual=response.status_code,
            cause="has_dangerous_control no detecta controles Unicode del stream.",
            priority="P0",
        )

        formula_filename = csv_file(
            self.artifacts,
            "formula_filename",
            [self.row(document_number=f"DOC-{RUN_SHORT}-FF", source_line_id="1")],
        )
        response, _, _ = self.upload_path(
            self.admin,
            formula_filename,
            tag="formula_filename",
            client_filename="=peligro.csv",
        )
        self.runner.check(
            "Nombre de archivo con fórmula peligrosa es rechazado",
            response.status_code == 400,
            expected=400,
            actual=response.status_code,
            cause="_safe_original_filename no valida prefijos de fórmula.",
            priority="P1",
        )
        return outcomes

    def row_validation_checks(self) -> dict[str, Any]:
        assert self.admin and self.active_product
        rows = [
            self.row(
                event_date="2025-02-30",
                document_number=f"DOC-{RUN_SHORT}-BADDATE",
                source_line_id="1",
            ),
            self.row(
                event_date="2024-12-31",
                document_number=f"DOC-{RUN_SHORT}-OUTYEAR",
                source_line_id="2",
            ),
            self.row(
                quantity="0",
                document_number=f"DOC-{RUN_SHORT}-ZERO",
                source_line_id="3",
            ),
            self.row(
                quantity="-1",
                document_number=f"DOC-{RUN_SHORT}-NEG",
                source_line_id="4",
            ),
            self.row(
                quantity="1.234",
                document_number=f"DOC-{RUN_SHORT}-SCALE",
                source_line_id="5",
            ),
            self.row(
                quantity="NaN",
                document_number=f"DOC-{RUN_SHORT}-NAN",
                source_line_id="6",
            ),
            self.row(
                quantity="inf",
                document_number=f"DOC-{RUN_SHORT}-INF",
                source_line_id="7",
            ),
            self.row(
                quantity="1e3",
                document_number=f"DOC-{RUN_SHORT}-SCI",
                source_line_id="8",
            ),
            self.row(
                code="=2+2",
                document_number=f"DOC-{RUN_SHORT}-FORMULA",
                source_line_id="9",
            ),
        ]
        path = csv_file(self.artifacts, "invalid_rows", rows)
        upload, _, tracked = self.upload_path(
            self.admin, path, tag="invalid_rows"
        )
        preview = None
        issues: list[dict[str, Any]] = []
        if tracked:
            preview = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{tracked['public_id']}/preview",
                json={},
            )
            if preview.status_code == 200:
                issues = self.errors(tracked["public_id"])
        by_row = {
            int(issue["source_row_number"]): issue
            for issue in issues
            if issue["source_row_number"] is not None
        }
        self.runner.check(
            "Fecha calendario inválida se registra como error",
            upload.status_code == 201
            and preview is not None
            and preview.status_code == 200
            and by_row.get(2, {}).get("error_code") == "invalid_event_date",
            base=9,
            expected="invalid_event_date en fila 2",
            actual=by_row.get(2, {}).get("error_code"),
            cause="parse_date_2025/validate_historical_row aceptó una fecha inexistente.",
            priority="P0",
        )
        self.runner.check(
            "Fecha fuera de 2025 se registra como error",
            by_row.get(3, {}).get("error_code") == "invalid_event_date"
            and "2025" in str(by_row.get(3, {}).get("message", "")),
            base=10,
            expected="invalid_event_date con regla 2025",
            actual=by_row.get(3, {}).get("error_code"),
            cause="parse_date_2025 no restringe el periodo histórico.",
            priority="P0",
        )
        self.runner.check(
            "Cantidad cero es inválida",
            by_row.get(4, {}).get("error_code") == "invalid_quantity",
            base=11,
            expected="invalid_quantity",
            actual=by_row.get(4, {}).get("error_code"),
            cause="parse_decimal_12_2 no exige quantity > 0.",
            priority="P0",
        )
        self.runner.check(
            "Cantidad negativa es inválida",
            by_row.get(5, {}).get("error_code") == "invalid_quantity",
            base=12,
            expected="invalid_quantity",
            actual=by_row.get(5, {}).get("error_code"),
            cause="parse_decimal_12_2 permite cantidades negativas.",
            priority="P0",
        )
        self.runner.check(
            "Cantidad con más de dos decimales es inválida",
            by_row.get(6, {}).get("error_code") == "invalid_quantity",
            expected="invalid_quantity",
            actual=by_row.get(6, {}).get("error_code"),
            cause="_DECIMAL_RE permite escala mayor a 2.",
            priority="P0",
        )
        self.runner.check(
            "NaN, infinito y notación científica son inválidos",
            all(
                by_row.get(number, {}).get("error_code") == "invalid_quantity"
                for number in (7, 8, 9)
            ),
            expected="tres invalid_quantity",
            actual=[by_row.get(number, {}).get("error_code") for number in (7, 8, 9)],
            cause="parse_decimal_12_2 acepta valores no finitos o científicos.",
            priority="P0",
        )
        self.runner.check(
            "Fórmula peligrosa en celda textual queda bloqueada",
            by_row.get(10, {}).get("error_code") == "invalid_product_code",
            expected="invalid_product_code",
            actual=by_row.get(10, {}).get("error_code"),
            cause="_text_field no valida starts_like_formula.",
            priority="P0",
        )
        return {
            "tracked": tracked,
            "preview": preview,
            "issues": issues,
        }

    def limit_and_mapping_checks(self) -> dict[str, Any]:
        assert self.admin and self.inventory and self.active_product
        extra_headers = tuple(f"extra_{index:02d}" for index in range(30))
        headers_40 = tuple(CSV_HEADERS) + extra_headers
        row = self.row(
            document_number=f"DOC-{RUN_SHORT}-MAP40",
            source_record_id=f"REC-{RUN_SHORT}-MAP40",
            source_line_id="1",
        )
        row.update({header: "ignorado" for header in extra_headers})
        path_40 = csv_file(
            self.artifacts, "headers_40", [row], headers=headers_40
        )
        upload_40, _, tracked_40 = self.upload_path(
            self.admin, path_40, tag="headers_40"
        )
        mapping = {name: name for name in CSV_HEADERS}
        preview_40 = None
        if tracked_40:
            preview_40 = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{tracked_40['public_id']}/preview",
                json={"mapping": mapping},
            )
        admin_detail_40 = (
            self.runner.request(
                self.admin,
                "GET",
                f"/api/historical-imports/{tracked_40['public_id']}",
            )
            if tracked_40
            else None
        )
        inventory_detail_40 = (
            self.runner.request(
                self.inventory,
                "GET",
                f"/api/historical-imports/{tracked_40['public_id']}",
            )
            if tracked_40 and self.inventory
            else None
        )
        admin_detail_payload = response_json(admin_detail_40) or {}
        inventory_detail_payload = response_json(inventory_detail_40) or {}
        records_40 = self.records(tracked_40["public_id"]) if tracked_40 else []
        errors_40 = self.errors(tracked_40["public_id"]) if tracked_40 else []
        self.runner.check(
            "Mapping explícito acepta exactamente 40 headers y conserva allowlist",
            upload_40.status_code == 201
            and preview_40 is not None
            and preview_40.status_code == 200
            and len(records_40) == 1
            and set((records_40[0].get("raw_row_json") or {})) == set(CSV_HEADERS)
            and admin_detail_payload.get("admin_metadata", {})
            .get("metadata", {})
            .get("headers")
            == list(headers_40)
            and "admin_metadata" not in inventory_detail_payload
            and any(
                issue["error_code"] == "unmapped_columns_ignored"
                and issue["severity"] == SEVERITY_WARNING
                for issue in errors_40
            ),
            expected="headers recuperables admin, preview 200 y raw_row allowlist",
            actual=(
                upload_40.status_code,
                preview_40.status_code if preview_40 else None,
                len(records_40),
            ),
            cause="resolve_column_mapping/canonicalize_csv_row no respeta el máximo y allowlist.",
            priority="P0",
        )

        bad_mapping = None
        if tracked_40:
            bad_mapping = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{tracked_40['public_id']}/preview",
                json={"mapping": {"event_date": "event_date"}},
            )
            self.runner.remember_error(bad_mapping)
        self.runner.check(
            "Mapping explícito incompleto devuelve 422",
            bad_mapping is not None and bad_mapping.status_code == 422,
            expected=422,
            actual=bad_mapping.status_code if bad_mapping else None,
            cause="resolve_column_mapping no exige todos los campos canónicos requeridos.",
            priority="P1",
        )

        headers_41 = headers_40 + ("extra_40",)
        row_41 = dict(row)
        row_41["extra_40"] = "x"
        path_41 = csv_file(
            self.artifacts, "headers_41", [row_41], headers=headers_41
        )
        upload_41, _, _ = self.upload_path(
            self.admin, path_41, tag="headers_41"
        )
        self.runner.check(
            "40+1 columnas devuelve 422",
            upload_41.status_code == 422,
            expected=422,
            actual=upload_41.status_code,
            cause="validate_headers no aplica MAX_COLUMNS=40.",
            priority="P0",
        )

        long_cell_row = self.row(
            name="A" * (MAX_CELL_CHARS + 1),
            document_number=f"DOC-{RUN_SHORT}-LONGCELL",
            source_line_id="1",
        )
        long_cell = csv_file(
            self.artifacts, "cell_4097", [long_cell_row]
        )
        upload_cell, _, _ = self.upload_path(
            self.admin, long_cell, tag="cell_4097"
        )
        self.runner.check(
            "Celda 4096+1 devuelve 422",
            upload_cell.status_code == 422,
            expected=422,
            actual=upload_cell.status_code,
            cause="_inspect_csv_structure no aplica MAX_CELL_CHARS=4096.",
            priority="P0",
        )

        rows_50001 = many_rows_file(
            self.artifacts,
            "rows_50001",
            self.active_product["code"],
            MAX_ROWS + 1,
        )
        upload_rows, _, _ = self.upload_path(
            self.admin, rows_50001, tag="rows_50001", timeout=180
        )
        self.runner.check(
            "50k+1 filas devuelve 413 en tiempo razonable",
            upload_rows.status_code == 413,
            expected=413,
            actual=upload_rows.status_code,
            cause="_inspect_csv_structure no aplica MAX_ROWS=50000 por streaming.",
            priority="P0",
        )
        return {"tracked_40": tracked_40}

    def pure_helper_checks(self) -> None:
        normalized_charset_literal = _normalize_sql("_utf8mb4'uploaded'")
        normalized_status_literal = _normalize_sql("'dry_run_ready'")
        status_signature = _check_signature(
            "status IN ('uploaded', 'previewed', 'dry_run_ready', "
            "'confirmed', 'reverted')"
        )
        self.runner.check(
            "Normalizador de CHECK conserva guiones bajos dentro de literales",
            normalized_charset_literal == "'uploaded'"
            and normalized_status_literal == "'dry_run_ready'"
            and "'dry_run_ready'" in status_signature,
            expected="introductor charset removido y dry_run_ready intacto",
            actual=(
                normalized_charset_literal,
                normalized_status_literal,
                status_signature,
            ),
            cause=(
                "La verificación de CHECK puede ocultar estados MySQL "
                "incompatibles al recortar sufijos de literales."
            ),
            priority="P0",
        )

        parsed, issues = validate_historical_row(
            {
                "event_date": "2025-01-01",
                "product_code": "  00123  ",
                "product_name": "",
                "quantity": "1.00",
                "record_type": "sale",
                "record_status": "issued",
                "document_number": " DOC-1 ",
                "source_record_id": "",
                "source_line_id": " 0001 ",
                "unit_price": "0",
            }
        )
        self.runner.check(
            "Normalización conserva original y preserva ceros iniciales",
            not issues
            and parsed is not None
            and parsed.values["product_code_original"] == "  00123  "
            and parsed.values["product_code_normalized"] == "00123"
            and parsed.values["source_line_id_normalized"] == "0001",
            expected="original exacto; normalizados 00123 y 0001",
            actual=(
                parsed.values.get("product_code_normalized") if parsed else None
            ),
            cause="La validación sobrescribe el código original o altera ceros.",
            priority="P1",
        )
        self.runner.check(
            "Diferencia de mayúsculas se normaliza sin fuzzy matching",
            normalize_code(" ab-c01 ") == "AB-C01",
            expected="AB-C01",
            actual=normalize_code(" ab-c01 "),
            cause="normalize_code no aplica uppercase exacto.",
            priority="P1",
        )
        nfc_code = normalize_code("  a\u0301 b/001-xy  ")
        self.runner.check(
            "NFC preserva espacios internos, barras, guiones y ceros",
            nfc_code == "Á B/001-XY",
            expected="Á B/001-XY",
            actual=nfc_code,
            cause="normalize_code altera caracteres internos o no aplica NFC.",
            priority="P0",
        )

        boundary_start, start_issues = validate_historical_row(
            {
                "event_date": "2025-01-01",
                "product_code": "BOUNDARY",
                "product_name": "",
                "quantity": "1",
                "record_type": "sale",
                "record_status": "issued",
                "document_number": "DOC-START",
                "source_record_id": "",
                "source_line_id": "1",
                "unit_price": "",
            }
        )
        boundary_end, end_issues = validate_historical_row(
            {
                "event_date": "2025-12-31",
                "product_code": "BOUNDARY",
                "product_name": "",
                "quantity": "1",
                "record_type": "sale",
                "record_status": "issued",
                "document_number": "DOC-END",
                "source_record_id": "",
                "source_line_id": "2",
                "unit_price": "",
            }
        )
        _, hour_issues = validate_historical_row(
            {
                "event_date": "2025-06-15T08:00:00-04:00",
                "product_code": "BOUNDARY",
                "product_name": "",
                "quantity": "1",
                "record_type": "sale",
                "record_status": "issued",
                "document_number": "DOC-HOUR",
                "source_record_id": "",
                "source_line_id": "3",
                "unit_price": "",
            }
        )
        _, overflow_issues = validate_historical_row(
            {
                "event_date": "2025-06-15",
                "product_code": "BOUNDARY",
                "product_name": "",
                "quantity": "10000000000.00",
                "record_type": "sale",
                "record_status": "issued",
                "document_number": "DOC-OVERFLOW",
                "source_record_id": "",
                "source_line_id": "4",
                "unit_price": "",
            }
        )
        self.runner.check(
            "Fechas límite 2025 son válidas y fecha-hora se rechaza",
            boundary_start is not None
            and boundary_end is not None
            and not start_issues
            and not end_issues
            and any(issue.error_code == "invalid_event_date" for issue in hour_issues),
            expected="01-01/12-31 válidas; timestamp inválido",
            actual=(len(start_issues), len(end_issues), len(hour_issues)),
            cause="parse_date_2025 no aplica exactamente YYYY-MM-DD y límites inclusivos.",
            priority="P0",
        )
        self.runner.check(
            "Cantidad que excede DECIMAL(12,2) se rechaza",
            any(issue.error_code == "invalid_quantity" for issue in overflow_issues),
            expected="invalid_quantity",
            actual=[issue.error_code for issue in overflow_issues],
            cause="parse_decimal_12_2 no aplica el máximo de DECIMAL(12,2).",
            priority="P0",
        )

        fakes = [
            SimpleNamespace(id=101, code="abc", name="Mismo nombre", is_active=True),
            SimpleNamespace(id=102, code=" ABC ", name="  mismo   nombre ", is_active=True),
        ]
        indexes = build_product_indexes(fakes)
        collision = match_product("ABC", normalize_name("otro"), indexes)
        ambiguous_name = match_product("NO-EXISTE", normalize_name("mismo nombre"), indexes)
        self.runner.check(
            "Conflicto de normalización se detecta con fakes sin insertar productos",
            "ABC" in indexes.normalized_code_collisions
            and collision.status == "code_collision",
            expected="code_collision",
            actual=collision.status,
            cause="build_product_indexes no detecta códigos operativos colisionados.",
            priority="P0",
        )
        self.runner.check(
            "Nombre ambiguo nunca produce auto-match ni sugerencia única",
            ambiguous_name.status == "unmatched"
            and ambiguous_name.product is None
            and ambiguous_name.suggested_product is None,
            expected="unmatched",
            actual=ambiguous_name.status,
            cause="match_product enlaza por nombre ambiguo.",
            priority="P0",
        )

        strong = build_fingerprint(
            source_system="SOURCE",
            document_type="historical_demand",
            document_number_normalized="DOC-1",
            source_line_id_normalized="1",
            event_date=date(2025, 1, 1),
            product_code_normalized="ABC",
            quantity=Decimal("1.00"),
            record_type="sale",
        )
        weak = build_fingerprint(
            source_system="SOURCE",
            document_type="historical_demand",
            document_number_normalized=None,
            source_line_id_normalized=None,
            event_date=date(2025, 1, 1),
            product_code_normalized="ABC",
            quantity=Decimal("1.00"),
            record_type="sale",
        )
        weak_without_document = build_fingerprint(
            source_system="SOURCE",
            document_type="historical_demand",
            document_number_normalized=None,
            source_line_id_normalized="1",
            event_date=date(2025, 1, 1),
            product_code_normalized="ABC",
            quantity=Decimal("1.00"),
            record_type="sale",
        )
        weak_without_line = build_fingerprint(
            source_system="SOURCE",
            document_type="historical_demand",
            document_number_normalized="DOC-1",
            source_line_id_normalized=None,
            event_date=date(2025, 1, 1),
            product_code_normalized="ABC",
            quantity=Decimal("1.00"),
            record_type="sale",
        )
        self.runner.check(
            "Fingerprints strong y weak son SHA-256 deterministas distintos",
            strong.strength == "strong"
            and weak.strength == "weak"
            and len(strong.value) == 64
            and len(weak.value) == 64
            and strong.value != weak.value,
            expected="strong/weak SHA-256 distintos",
            actual=(strong.strength, weak.strength),
            cause="build_fingerprint no clasifica la fuerza por documento/línea.",
            priority="P0",
        )

        try:
            resolve_column_mapping(
                list(CSV_HEADERS),
                {"event_date": "event_date"},
            )
            mapping_failed = False
        except Exception:
            mapping_failed = True
        self.runner.check(
            "Helper puro exige mapping explícito completo",
            mapping_failed,
            expected="HeaderValidationError",
            actual=mapping_failed,
            cause="resolve_column_mapping acepta mappings incompletos.",
            priority="P1",
        )

        permitted_statuses = {
            "sale": {"issued", "active", "cancelled", "voided", "superseded"},
            "return": {"issued", "active", "cancelled", "voided", "superseded"},
            "cancellation": {"issued", "active", "voided", "superseded"},
            "correction": {"issued", "active", "voided", "superseded"},
        }
        matrix_ok = True
        for record_type in ("sale", "return", "cancellation", "correction"):
            for record_status in (
                "issued",
                "active",
                "cancelled",
                "voided",
                "superseded",
            ):
                matrix_row = self.row(
                    record_type=record_type,
                    record_status=record_status,
                    document_number=f"DOC-{RUN_SHORT}-MATRIX",
                    source_line_id=f"{record_type}-{record_status}",
                )
                parsed, matrix_issues = validate_historical_row(matrix_row)
                expected_valid = record_status in permitted_statuses[record_type]
                actual_valid = parsed is not None and not matrix_issues
                matrix_ok = matrix_ok and actual_valid == expected_valid
        self.runner.check(
            "Matriz completa record_type/record_status aplica la política v1",
            matrix_ok,
            expected="20 combinaciones evaluadas según allowlist",
            actual=matrix_ok,
            cause="ALLOWED_TYPE_STATUS no coincide con la política documentada.",
            priority="P0",
        )

    def valid_lifecycle_checks(self) -> dict[str, Any]:
        assert self.admin and self.active_product
        operational_before_upload = self.db_snapshot(OPERATIONAL_TABLES)
        original_code = self.active_product["code"]
        row = self.row(
            code=f"  {original_code.lower()}  ",
            name=self.active_product["name"],
            quantity="2.00",
            document_number=f"DOC-{RUN_SHORT}-VALID",
            source_record_id=f"REC-{RUN_SHORT}-VALID",
            source_line_id="0001",
            unit_price="1.25",
        )
        path = csv_file(self.artifacts, "valid_lifecycle", [row])
        self.valid_path = path
        self.valid_original_filename = path.name
        upload, payload, tracked = self.upload_path(
            self.admin, path, tag="valid_lifecycle"
        )
        self.valid_import = tracked
        self.runner.check(
            "CSV válido con BOM se guarda en privado",
            upload.status_code == 201
            and tracked is not None
            and isinstance(payload, dict)
            and not recursive_contains_key(payload, "storage_key"),
            base=3,
            expected="HTTP 201 sin storage_key",
            actual=upload.status_code,
            cause="upload_import/controller incumple el contrato de upload privado.",
            priority="P0",
        )
        if not tracked:
            raise RuntimeError("No se creó el lote válido principal")

        uploaded_state = self.import_state(tracked["public_id"])
        self.runner.check(
            "Upload solo conserva archivo/metadatos; no importa ni crea staging",
            uploaded_state is not None
            and uploaded_state["status"] == "uploaded"
            and uploaded_state["counts"]["total"] == 0
            and not self.records(tracked["public_id"])
            and not self.errors(tracked["public_id"])
            and self.db_snapshot(OPERATIONAL_TABLES) == operational_before_upload,
            expected="uploaded, cero records/errors y operativa idéntica",
            actual=(
                uploaded_state["status"] if uploaded_state else None,
                uploaded_state["counts"] if uploaded_state else None,
            ),
            cause="upload_import ejecutó parsing/importación automática.",
            priority="P0",
        )
        fingerprint_parameters = set(
            python_inspect.signature(build_fingerprint).parameters
        )
        repeated_strong = build_fingerprint(
            source_system="SOURCE",
            document_type="historical_demand",
            document_number_normalized="DOC-1",
            source_line_id_normalized="1",
            event_date=date(2025, 1, 1),
            product_code_normalized="ABC",
            quantity=Decimal("1"),
            record_type="sale",
        )
        strong = build_fingerprint(
            source_system="SOURCE",
            document_type="historical_demand",
            document_number_normalized="DOC-1",
            source_line_id_normalized="1",
            event_date=date(2025, 1, 1),
            product_code_normalized="ABC",
            quantity=Decimal("1.00"),
            record_type="sale",
        )
        weak_without_document = build_fingerprint(
            source_system="SOURCE",
            document_type="historical_demand",
            document_number_normalized=None,
            source_line_id_normalized="1",
            event_date=date(2025, 1, 1),
            product_code_normalized="ABC",
            quantity=Decimal("1.00"),
            record_type="sale",
        )
        weak_without_line = build_fingerprint(
            source_system="SOURCE",
            document_type="historical_demand",
            document_number_normalized="DOC-1",
            source_line_id_normalized=None,
            event_date=date(2025, 1, 1),
            product_code_normalized="ABC",
            quantity=Decimal("1.00"),
            record_type="sale",
        )
        lower_line = build_fingerprint(
            source_system="SOURCE",
            document_type="historical_demand",
            document_number_normalized="DOC-1",
            source_line_id_normalized="line-a",
            event_date=date(2025, 1, 1),
            product_code_normalized="ABC",
            quantity=Decimal("1.00"),
            record_type="sale",
        )
        upper_line = build_fingerprint(
            source_system="SOURCE",
            document_type="historical_demand",
            document_number_normalized="DOC-1",
            source_line_id_normalized="LINE-A",
            event_date=date(2025, 1, 1),
            product_code_normalized="ABC",
            quantity=Decimal("1.00"),
            record_type="sale",
        )
        self.runner.check(
            "Fingerprint omite filename/fila, conserva case de línea y clasifica weak",
            "filename" not in fingerprint_parameters
            and "source_row_number" not in fingerprint_parameters
            and repeated_strong.value == strong.value
            and weak_without_document.strength == "weak"
            and weak_without_line.strength == "weak"
            and lower_line.value != upper_line.value,
            expected="firma estable; IDs weak; source_line case-sensitive",
            actual=(
                sorted(fingerprint_parameters),
                weak_without_document.strength,
                weak_without_line.strength,
            ),
            cause="build_fingerprint incluye trazabilidad física o clasifica weak incorrectamente.",
            priority="P0",
        )

        file_hash, file_size = sha256_file(path)
        private = self.private_path(tracked["storage_key"])
        private_hash, private_size = sha256_file(private)
        self.runner.check(
            "SHA-256 y tamaño registrados coinciden byte a byte",
            tracked["sha256"] == file_hash == private_hash
            and file_size == private_size,
            base=30,
            expected=(file_hash, file_size),
            actual=(tracked["sha256"], private_size),
            cause="_copy_upload_to_private/sha256_file no conserva integridad.",
            priority="P0",
        )

        preview_1 = self.runner.request(
            self.admin,
            "POST",
            f"/api/historical-imports/{tracked['public_id']}/preview",
            json={},
        )
        records_1 = self.canonical_records(self.records(tracked["public_id"]))
        preview_2 = self.runner.request(
            self.admin,
            "POST",
            f"/api/historical-imports/{tracked['public_id']}/preview",
            json={},
        )
        records_2 = self.canonical_records(self.records(tracked["public_id"]))
        self.runner.check(
            "Preview reproducible genera los mismos hechos y fingerprints",
            preview_1.status_code == 200
            and preview_2.status_code == 200
            and records_1 == records_2
            and len(records_2) == 1,
            base=34,
            expected="dos previews canónicos idénticos",
            actual=(preview_1.status_code, preview_2.status_code, len(records_2)),
            cause="_parse_preview_file/fingerprint depende del ID o número físico mutable.",
            priority="P0",
        )
        record = self.records(tracked["public_id"])[0]
        self.runner.check(
            "Código exacto normalizado enlaza el producto activo real",
            record["product_id"] == self.active_product["id"]
            and record["match_status"] == "exact"
            and record["product_code_normalized"] == normalize_code(original_code),
            base=13,
            expected=self.active_product["id"],
            actual=record["product_id"],
            cause="historical_matching_service no prioriza código normalizado exacto.",
            priority="P0",
        )

        state = self.import_state(tracked["public_id"])
        self.runner.check(
            "Trazabilidad registra mapping, versiones, actor y timestamps de preview",
            state is not None
            and state["created_by_user_id"] == self.admin_user_id
            and state["previewed_by_user_id"] == self.admin_user_id
            and state["created_at"] is not None
            and state["previewed_at"] is not None
            and state["mapping"] == {name: name for name in CSV_HEADERS}
            and state["metadata"].get("column_count") == len(CSV_HEADERS)
            and state["counts"]
            == {
                "total": 1,
                "valid": 1,
                "errors": 0,
                "warnings": 0,
                "reviews": 0,
                "matched": 1,
                "strong": 1,
                "weak": 0,
            },
            base=29,
            expected="auditoría completa",
            actual=state,
            cause="HistoricalImport no persiste actor/mapping/metadata de preview.",
            priority="P0",
        )

        before_dry_run = self.db_snapshot(OPERATIONAL_TABLES)
        dry_run = self.runner.request(
            self.admin,
            "POST",
            f"/api/historical-imports/{tracked['public_id']}/dry-run",
        )
        dry_payload = response_json(dry_run) or {}
        token = dry_payload.get("confirmation_token")
        after_dry_run = self.db_snapshot(OPERATIONAL_TABLES)
        staged = self.records(tracked["public_id"])
        self.runner.check(
            "Dry run no activa demanda ni toca tablas operativas",
            dry_run.status_code == 200
            and isinstance(token, str)
            and token
            and before_dry_run == after_dry_run
            and all(not bool(item["include_in_demand"]) for item in staged)
            and dry_payload.get("summary", {}).get("activates_demand") is False,
            base=22,
            expected="HTTP 200, token y cero efecto",
            actual=dry_run.status_code,
            cause="dry_run_import modifica demanda/operativa antes de confirmación.",
            priority="P0",
        )
        if not isinstance(token, str):
            raise RuntimeError("Dry run principal no entregó token")

        invalid = self.runner.request(
            self.admin,
            "POST",
            f"/api/historical-imports/{tracked['public_id']}/confirm",
            json={"confirmation_token": "token-invalido"},
        )
        self.runner.remember_error(invalid)
        self.runner.check(
            "Token de confirmación inválido devuelve 409 y no transiciona",
            invalid.status_code == 409
            and self.import_state(tracked["public_id"])["status"] == "dry_run_ready",
            expected=409,
            actual=invalid.status_code,
            cause="confirm_import no compara el hash del token antes de confirmar.",
            priority="P0",
        )

        concurrent_admin, concurrent_password = self.credentials[ROLE_ADMIN]
        concurrent_sessions = [
            self.login(
                concurrent_admin,
                concurrent_password,
                f"admin concurrente {index}",
            )
            for index in (1, 2)
        ]

        def confirm_once(session: requests.Session) -> tuple[int, Any]:
            response = self.runner.request(
                session,
                "POST",
                f"/api/historical-imports/{tracked['public_id']}/confirm",
                json={"confirmation_token": token},
                timeout=60,
            )
            return response.status_code, response_json(response)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(confirm_once, concurrent_sessions))
        replay_flags = [
            payload.get("idempotent_replay")
            for status, payload in results
            if status == 200 and isinstance(payload, dict)
        ]
        self.runner.check(
            "Confirmación concurrente produce exactamente una transición",
            [status for status, _ in results] == [200, 200]
            and replay_flags.count(False) == 1
            and replay_flags.count(True) == 1
            and self.import_state(tracked["public_id"])["status"] == "confirmed",
            base=23,
            expected="dos 200: una transición y un replay",
            actual=([status for status, _ in results], replay_flags),
            cause="confirm_import/SELECT FOR UPDATE no serializa confirmaciones concurrentes.",
            priority="P0",
        )

        confirmed_records = self.records(tracked["public_id"])
        self.runner.check(
            "Confirmación activa solo el hecho histórico y no stock",
            len(confirmed_records) == 1
            and confirmed_records[0]["include_in_demand"] is True
            and confirmed_records[0]["dedupe_key"]
            == confirmed_records[0]["fingerprint"],
            expected="include_in_demand=true y dedupe fuerte",
            actual=confirmed_records[0] if confirmed_records else None,
            cause="confirm_import no aplica include_in_demand/dedupe de forma atómica.",
            priority="P0",
        )

        immutable_before = stable_hash(confirmed_records)
        confirmed_state_before = self.import_state(tracked["public_id"])
        preview_after_confirm = self.runner.request(
            self.admin,
            "POST",
            f"/api/historical-imports/{tracked['public_id']}/preview",
            json={},
        )
        review_after_confirm = self.runner.request(
            self.admin,
            "POST",
            (
                f"/api/historical-imports/{tracked['public_id']}"
                f"/records/{confirmed_records[0]['id']}/review"
            ),
            json={"product_id": self.active_product["id"]},
        )
        self.runner.remember_error(preview_after_confirm)
        self.runner.remember_error(review_after_confirm)
        confirmed_state_after = self.import_state(tracked["public_id"])
        self.runner.check(
            "Filas confirmadas son inmutables ante preview y review posteriores",
            preview_after_confirm.status_code == 409
            and review_after_confirm.status_code == 409
            and stable_hash(self.records(tracked["public_id"])) == immutable_before
            and confirmed_state_before == confirmed_state_after,
            expected="dos 409 y hashes/estado idénticos",
            actual=(preview_after_confirm.status_code, review_after_confirm.status_code),
            cause="Un endpoint de staging permite mutar un lote confirmado.",
            priority="P0",
        )

        confirmed_record_id = int(confirmed_records[0]["id"])
        orm_update_blocked = False
        sql_update_blocked = False
        sql_delete_blocked = False
        with self.context():
            record = db.session.get(HistoricalDemandRecord, confirmed_record_id)
            assert record is not None
            record.quantity = Decimal("999.00")
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                orm_update_blocked = True
        with self.context():
            try:
                db.session.execute(
                    HistoricalDemandRecord.__table__.update()
                    .where(HistoricalDemandRecord.id == confirmed_record_id)
                    .values(quantity=Decimal("998.00"))
                )
                db.session.commit()
            except Exception:
                db.session.rollback()
                sql_update_blocked = True
        with self.context():
            try:
                db.session.execute(
                    HistoricalDemandRecord.__table__.delete().where(
                        HistoricalDemandRecord.id == confirmed_record_id
                    )
                )
                db.session.commit()
            except Exception:
                db.session.rollback()
                sql_delete_blocked = True
        self.runner.check(
            "Inmutabilidad bloquea ORM, UPDATE SQL y DELETE SQL confirmados",
            orm_update_blocked
            and sql_update_blocked
            and sql_delete_blocked
            and stable_hash(self.records(tracked["public_id"])) == immutable_before,
            expected="tres bloqueos y registro idéntico",
            actual=(orm_update_blocked, sql_update_blocked, sql_delete_blocked),
            cause="Listeners o triggers de hechos históricos no están activos.",
            priority="P0",
        )

        replay = self.runner.request(
            self.admin,
            "POST",
            f"/api/historical-imports/{tracked['public_id']}/confirm",
            json={"confirmation_token": token},
        )
        replay_payload = response_json(replay) or {}
        self.runner.check(
            "Replay del token es idempotente",
            replay.status_code == 200
            and replay_payload.get("idempotent_replay") is True
            and stable_hash(self.records(tracked["public_id"]))
            == stable_hash(confirmed_records),
            expected="200 idempotent_replay=true",
            actual=(replay.status_code, replay_payload.get("idempotent_replay")),
            cause="confirm_import aplica un segundo efecto al repetir el token.",
            priority="P0",
        )

        logical_fields = {
            "include_in_demand",
            "effective_status",
            "superseded_by_record_id",
            "superseded_by_import_id",
            "superseded_at",
            "updated_at",
            "lock_version",
        }

        def immutable_facts(rows: list[dict[str, Any]]) -> str:
            return stable_hash(
                [
                    {
                        key: value
                        for key, value in row.items()
                        if key not in logical_fields
                    }
                    for row in rows
                ]
            )

        records_before_revert = immutable_facts(
            self.records(tracked["public_id"])
        )
        missing_reason = self.runner.request(
            self.admin,
            "POST",
            f"/api/historical-imports/{tracked['public_id']}/revert",
            json={},
        )
        self.runner.remember_error(missing_reason)
        revert = self.runner.request(
            self.admin,
            "POST",
            f"/api/historical-imports/{tracked['public_id']}/revert",
            json={"reason": f"Reversión controlada {RUN_SHORT}"},
        )
        rows_after_revert = self.records(tracked["public_id"])
        records_after_revert = immutable_facts(rows_after_revert)
        second_revert = self.runner.request(
            self.admin,
            "POST",
            f"/api/historical-imports/{tracked['public_id']}/revert",
            json={"reason": "Este motivo no debe reemplazar el primero"},
        )
        second_payload = response_json(second_revert) or {}
        state_after_revert = self.import_state(tracked["public_id"])
        self.runner.check(
            "Revert exige motivo, es lógico, conserva records y doble revert es seguro",
            missing_reason.status_code == 400
            and revert.status_code == 200
            and second_revert.status_code == 200
            and second_payload.get("idempotent_replay") is True
            and state_after_revert["status"] == "reverted"
            and state_after_revert["reversal_reason"]
            == f"Reversión controlada {RUN_SHORT}"
            and records_before_revert == records_after_revert
            and all(not bool(row["include_in_demand"]) for row in rows_after_revert),
            base=25,
            expected="400; 200 lógico; replay 200; hechos inmutables",
            actual=(
                missing_reason.status_code,
                revert.status_code,
                second_revert.status_code,
                state_after_revert["status"],
            ),
            cause="revert_import no exige motivo o altera/borrar registros confirmados.",
            priority="P0",
        )

        duplicate, _, _ = self.upload_path(
            self.admin,
            path,
            tag="same_sha_after_revert",
            source_system=self.source("same_sha_after_revert"),
        )
        self.runner.remember_error(duplicate)
        self.runner.check(
            "Mismo archivo/SHA es rechazado incluso tras revertir",
            duplicate.status_code == 409 and safe_error_json(duplicate, 409),
            base=19,
            expected=409,
            actual=duplicate.status_code,
            cause="upload_import no mantiene SHA único para lotes revertidos.",
            priority="P0",
        )
        return {"tracked": tracked, "token": token}

    def expired_token_check(self) -> None:
        assert self.admin
        row = self.row(
            document_number=f"DOC-{RUN_SHORT}-EXPIRED",
            source_line_id="1",
        )
        path = csv_file(self.artifacts, "expired_token", [row])
        upload, _, tracked = self.upload_path(
            self.admin, path, tag="expired_token"
        )
        token = None
        if tracked:
            self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{tracked['public_id']}/preview",
                json={},
            )
            dry = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{tracked['public_id']}/dry-run",
            )
            token = (response_json(dry) or {}).get("confirmation_token")
            with self.context():
                item = HistoricalImport.query.filter_by(
                    public_id=tracked["public_id"]
                ).one()
                item.confirmation_token_expires_at = datetime.utcnow() - timedelta(
                    seconds=1
                )
                db.session.commit()
        confirm = None
        if tracked and isinstance(token, str):
            confirm = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{tracked['public_id']}/confirm",
                json={"confirmation_token": token},
            )
            self.runner.remember_error(confirm)
        state = self.import_state(tracked["public_id"]) if tracked else None
        self.runner.check(
            "Token expirado devuelve 409, invalida token y vuelve a preview",
            upload.status_code == 201
            and confirm is not None
            and confirm.status_code == 409
            and state is not None
            and state["status"] == "previewed"
            and state["confirmation_token_hash"] is None,
            expected="409 y status=previewed",
            actual=(confirm.status_code if confirm else None, state),
            cause="confirm_import no valida confirmation_token_expires_at.",
            priority="P0",
        )

    def matching_review_checks(self) -> dict[str, Any]:
        assert self.admin and self.inventory and self.active_product
        unknown_code = f"NO-{RUN_SHORT}"
        rows = [
            self.row(
                code=f"{unknown_code}-SUG",
                name=self.active_product["name"],
                document_number=f"DOC-{RUN_SHORT}-SUG",
                source_line_id="1",
            ),
            self.row(
                code=f"{unknown_code}-NONE",
                name=f"Producto inexistente {RUN_SHORT}",
                document_number=f"DOC-{RUN_SHORT}-NONE",
                source_line_id="2",
            ),
        ]
        path = csv_file(self.artifacts, "matching_reviews", rows)
        upload, _, tracked = self.upload_path(
            self.admin, path, tag="matching_reviews"
        )
        preview = None
        records: list[dict[str, Any]] = []
        if tracked:
            preview = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{tracked['public_id']}/preview",
                json={},
            )
            records = self.records(tracked["public_id"])
        suggested = records[0] if len(records) > 0 else {}
        unmatched = records[1] if len(records) > 1 else {}
        self.runner.check(
            "Nombre solo crea sugerencia; nunca auto-match",
            upload.status_code == 201
            and preview is not None
            and preview.status_code == 200
            and suggested.get("product_id") is None
            and suggested.get("suggested_product_id") == self.active_product["id"]
            and suggested.get("match_status") == "name_suggested",
            base=14,
            expected="product_id=None, suggested_product_id real",
            actual=(
                suggested.get("product_id"),
                suggested.get("suggested_product_id"),
                suggested.get("match_status"),
            ),
            cause="_rebuild_dynamic_issues/match_product auto-enlaza por nombre.",
            priority="P0",
        )
        self.runner.check(
            "Producto inexistente queda unmatched y exige revisión",
            unmatched.get("product_id") is None
            and unmatched.get("suggested_product_id") is None
            and unmatched.get("match_status") == "unmatched",
            base=16,
            expected="unmatched sin IDs",
            actual=(
                unmatched.get("product_id"),
                unmatched.get("suggested_product_id"),
                unmatched.get("match_status"),
            ),
            cause="matching histórico enlaza un código/nombre inexistente.",
            priority="P0",
        )

        inventory_review = None
        admin_review = None
        if tracked and suggested.get("id"):
            endpoint = (
                f"/api/historical-imports/{tracked['public_id']}"
                f"/records/{suggested['id']}/review"
            )
            inventory_review = self.runner.request(
                self.inventory,
                "POST",
                endpoint,
                json={"product_id": self.active_product["id"]},
            )
            self.runner.remember_error(inventory_review)
            admin_review = self.runner.request(
                self.admin,
                "POST",
                endpoint,
                json={
                    "product_id": self.active_product["id"],
                    "approve": ["manual_match"],
                },
            )
        reviewed = (
            next(
                (
                    item
                    for item in self.records(tracked["public_id"])
                    if item["source_row_number"] == 2
                ),
                {},
            )
            if tracked
            else {}
        )
        self.runner.check(
            "Solo admin puede convertir sugerencia de nombre en match manual",
            inventory_review is not None
            and inventory_review.status_code == 403
            and admin_review is not None
            and admin_review.status_code == 200
            and reviewed.get("product_id") == self.active_product["id"]
            and reviewed.get("match_method") == "manual_name_admin",
            expected="inventario 403; admin 200 auditado",
            actual=(
                inventory_review.status_code if inventory_review else None,
                admin_review.status_code if admin_review else None,
            ),
            cause="review_record/permisos permiten auto-match o revisión no administrativa.",
            priority="P0",
        )

        dry = None
        if tracked:
            dry = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{tracked['public_id']}/dry-run",
            )
            self.runner.remember_error(dry)
        self.runner.check(
            "Cualquier review no resuelto bloquea dry run/confirmación",
            dry is not None and dry.status_code == 422,
            expected=422,
            actual=dry.status_code if dry else None,
            cause="_blocking_issue_count no incluye revisiones unresolved.",
            priority="P0",
        )
        return {"tracked": tracked}

    def ambiguity_and_duplicate_checks(self) -> None:
        assert self.admin
        document = f"DOC-{RUN_SHORT}-AMB"
        rows = [
            self.row(quantity="5", document_number=document, source_line_id="S1"),
            self.row(quantity="6", document_number=document, source_line_id="S2"),
            self.row(
                quantity="1",
                record_type="return",
                document_number=document,
                source_line_id="R1",
            ),
        ]
        path = csv_file(self.artifacts, "ambiguous_relation", rows)
        _, _, tracked = self.upload_path(
            self.admin, path, tag="ambiguous_relation"
        )
        if tracked:
            self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{tracked['public_id']}/preview",
                json={},
            )
        errors = self.errors(tracked["public_id"]) if tracked else []
        ambiguous_tracked = tracked
        self.runner.check(
            "Relación ambigua se detecta y no se elige automáticamente",
            any(
                issue["error_code"] == "related_record_ambiguous"
                and issue["severity"] == SEVERITY_REVIEW
                for issue in errors
            ),
            base=15,
            expected="related_record_ambiguous",
            actual=[issue["error_code"] for issue in errors],
            cause="_rebuild_dynamic_issues elige una venta cuando existen varias candidatas.",
            priority="P0",
        )

        ambiguous_records = (
            self.records(ambiguous_tracked["public_id"])
            if ambiguous_tracked
            else []
        )
        return_record = next(
            (item for item in ambiguous_records if item["record_type"] == "return"),
            None,
        )
        candidates = foreign_record = None
        if ambiguous_tracked and return_record and self.valid_import:
            candidates = self.runner.request(
                self.admin,
                "GET",
                (
                    f"/api/historical-imports/{ambiguous_tracked['public_id']}"
                    f"/records/{return_record['id']}/relationship-candidates"
                    "?page=1&per_page=1"
                ),
            )
            foreign_record = self.runner.request(
                self.admin,
                "GET",
                (
                    f"/api/historical-imports/{self.valid_import['public_id']}"
                    f"/records/{return_record['id']}/relationship-candidates"
                ),
            )
            self.runner.remember_error(foreign_record)
        candidate_payload = response_json(candidates) or {}
        candidate_items = candidate_payload.get("items", [])
        self.runner.check(
            "Candidatos de relación son paginados, mínimos y del mismo lote",
            candidates is not None
            and candidates.status_code == 200
            and len(candidate_items) == 1
            and candidate_payload.get("pagination", {}).get("total") == 2
            and set(candidate_items[0])
            == {"id", "source_row_number", "record_type", "event_date", "quantity"}
            and not recursive_contains_key(candidate_payload, "storage_key")
            and not recursive_contains_key(candidate_payload, "raw_row_json")
            and foreign_record is not None
            and foreign_record.status_code == 404,
            expected="1/2 candidatos allowlist y record ajeno 404",
            actual=(
                candidates.status_code if candidates else None,
                candidate_payload.get("pagination"),
                foreign_record.status_code if foreign_record else None,
            ),
            cause="relationship-candidates mezcla lotes o expone datos internos.",
            priority="P0",
        )

        duplicate_row = self.row(
            quantity="3",
            document_number=f"DOC-{RUN_SHORT}-DUP",
            source_record_id=f"REC-{RUN_SHORT}-DUP",
            source_line_id="1",
        )
        path = csv_file(
            self.artifacts, "duplicate_in_file", [duplicate_row, duplicate_row]
        )
        _, _, tracked = self.upload_path(
            self.admin, path, tag="duplicate_in_file"
        )
        if tracked:
            self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{tracked['public_id']}/preview",
                json={},
            )
        errors = self.errors(tracked["public_id"]) if tracked else []
        duplicate_errors = [
            issue
            for issue in errors
            if issue["error_code"] == "strong_duplicate_in_file"
        ]
        self.runner.check(
            "Duplicado fuerte dentro del archivo bloquea ambas filas",
            len(duplicate_errors) == 2
            and all(issue["severity"] == SEVERITY_ERROR for issue in duplicate_errors),
            base=18,
            expected="2 strong_duplicate_in_file",
            actual=len(duplicate_errors),
            cause="_rebuild_dynamic_issues no cuenta fingerprints fuertes repetidos.",
            priority="P0",
        )

        duplicate_filter = invalid_filter = None
        if tracked:
            duplicate_filter = self.runner.request(
                self.admin,
                "GET",
                (
                    f"/api/historical-imports/{tracked['public_id']}/errors"
                    "?category=duplicate&page=1&per_page=1"
                ),
            )
            invalid_filter = self.runner.request(
                self.admin,
                "GET",
                f"/api/historical-imports/{tracked['public_id']}/errors?category=otro",
            )
            self.runner.remember_error(invalid_filter)
        duplicate_payload = response_json(duplicate_filter) or {}
        filtered_items = duplicate_payload.get("items", [])
        self.runner.check(
            "Filtro duplicate pagina solo hallazgos de duplicidad y valida categoría",
            duplicate_filter is not None
            and duplicate_filter.status_code == 200
            and len(filtered_items) == 1
            and duplicate_payload.get("pagination", {}).get("total") == 2
            and all(
                "duplicate" in str(item.get("code", ""))
                for item in filtered_items
            )
            and invalid_filter is not None
            and invalid_filter.status_code == 400,
            expected="1/2 duplicate; categoría inválida 400",
            actual=(
                duplicate_payload.get("pagination"),
                invalid_filter.status_code if invalid_filter else None,
            ),
            cause="list_errors no aplica category=duplicate de forma segura.",
            priority="P1",
        )

    def inactive_product_check(self) -> None:
        if self.inactive_product is None:
            self.runner.skip(
                "Producto inactivo requiere review admin antes de confirmar",
                "No existe producto inactivo real y está prohibido crearlo.",
            )
            return
        assert self.admin and self.inventory
        row = self.row(
            code=self.inactive_product["code"],
            name=self.inactive_product["name"],
            document_number=f"DOC-{RUN_SHORT}-INACTIVE",
            source_line_id="1",
        )
        path = csv_file(self.artifacts, "inactive_product", [row])
        upload, _, tracked = self.upload_path(
            self.admin, path, tag="inactive_product"
        )
        if not tracked:
            self.runner.check(
                "Producto inactivo requiere review admin antes de confirmar",
                False,
                base=17,
                expected="upload 201",
                actual=upload.status_code,
                cause="No se pudo crear el lote histórico de producto inactivo.",
                priority="P0",
            )
            return
        self.runner.request(
            self.admin,
            "POST",
            f"/api/historical-imports/{tracked['public_id']}/preview",
            json={},
        )
        record = self.records(tracked["public_id"])[0]
        blocked = self.runner.request(
            self.admin,
            "POST",
            f"/api/historical-imports/{tracked['public_id']}/dry-run",
        )
        self.runner.remember_error(blocked)
        endpoint = (
            f"/api/historical-imports/{tracked['public_id']}"
            f"/records/{record['id']}/review"
        )
        inventory_review = self.runner.request(
            self.inventory,
            "POST",
            endpoint,
            json={"approve": ["inactive_product"]},
        )
        admin_review = self.runner.request(
            self.admin,
            "POST",
            endpoint,
            json={"approve": ["inactive_product"]},
        )
        dry = self.runner.request(
            self.admin,
            "POST",
            f"/api/historical-imports/{tracked['public_id']}/dry-run",
        )
        token = (response_json(dry) or {}).get("confirmation_token")
        confirm = None
        if isinstance(token, str):
            confirm = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{tracked['public_id']}/confirm",
                json={"confirmation_token": token},
            )
        final_record = self.records(tracked["public_id"])[0]
        with self.context():
            product_still_inactive = not bool(
                db.session.get(Product, self.inactive_product["id"]).is_active
            )
        self.runner.check(
            "Producto inactivo solo entra en demanda tras review admin",
            record["match_status"] == "inactive_review"
            and record["include_in_demand"] is False
            and blocked.status_code == 422
            and inventory_review.status_code == 403
            and admin_review.status_code == 200
            and dry.status_code == 200
            and confirm is not None
            and confirm.status_code == 200
            and final_record["include_in_demand"] is True
            and final_record["review_flags_json"].get("inactive_product") is True
            and product_still_inactive,
            base=17,
            expected="bloqueado -> review admin -> confirmado sin activar producto",
            actual=(
                blocked.status_code,
                inventory_review.status_code,
                admin_review.status_code,
                dry.status_code,
                confirm.status_code if confirm else None,
            ),
            cause="Matching/review permite producto inactivo sin aprobación administrativa.",
            priority="P0",
        )

    def weak_fingerprint_checks(self) -> None:
        assert self.admin
        shared_source = self.source("weak_shared")
        fingerprints: list[str] = []
        dedupe_keys: list[Any] = []
        statuses: list[int] = []
        for index in (1, 2):
            row = self.row(
                name="",
                document_number="",
                source_record_id=f"WEAK-{RUN_SHORT}-{index}",
                source_line_id="",
            )
            path = csv_file(self.artifacts, f"weak_{index}", [row])
            upload, _, tracked = self.upload_path(
                self.admin,
                path,
                tag=f"weak_{index}",
                source_system=shared_source,
            )
            if not tracked:
                statuses.append(upload.status_code)
                continue
            self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{tracked['public_id']}/preview",
                json={},
            )
            record = self.records(tracked["public_id"])[0]
            blocked = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{tracked['public_id']}/dry-run",
            )
            review = self.runner.request(
                self.admin,
                "POST",
                (
                    f"/api/historical-imports/{tracked['public_id']}"
                    f"/records/{record['id']}/review"
                ),
                json={"approve": ["weak_duplicate"]},
            )
            dry = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{tracked['public_id']}/dry-run",
            )
            token = (response_json(dry) or {}).get("confirmation_token")
            confirm = None
            if isinstance(token, str):
                confirm = self.runner.request(
                    self.admin,
                    "POST",
                    f"/api/historical-imports/{tracked['public_id']}/confirm",
                    json={"confirmation_token": token},
                )
            final = self.records(tracked["public_id"])[0]
            statuses.extend(
                [
                    blocked.status_code,
                    review.status_code,
                    dry.status_code,
                    confirm.status_code if confirm else 0,
                ]
            )
            fingerprints.append(final["fingerprint"])
            dedupe_keys.append(final["dedupe_key"])
        self.runner.check(
            "Fingerprint weak exige review y nunca auto-dedupe",
            len(fingerprints) == 2
            and fingerprints[0] == fingerprints[1]
            and dedupe_keys == [None, None]
            and statuses == [422, 200, 200, 200, 422, 200, 200, 200],
            expected="dos weak iguales confirmados tras review con dedupe_key NULL",
            actual=(len(fingerprints), dedupe_keys, statuses),
            cause="weak_possible_duplicate no bloquea review o llena dedupe_key automática.",
            priority="P0",
        )

    def effects_checks(self) -> dict[str, Any]:
        assert self.admin
        doc1 = f"DOC-{RUN_SHORT}-EFFECT-1"
        doc2 = f"DOC-{RUN_SHORT}-EFFECT-2"
        rows = [
            self.row(quantity="10", document_number=doc1, source_line_id="S1"),
            self.row(
                quantity="2",
                record_type="return",
                document_number=doc1,
                source_line_id="R1",
            ),
            self.row(
                quantity="3",
                record_type="cancellation",
                document_number=doc1,
                source_line_id="C1",
            ),
            self.row(quantity="4", document_number=doc2, source_line_id="S2"),
            self.row(
                quantity="4",
                record_type="cancellation",
                record_status="voided",
                document_number=doc2,
                source_line_id="C2",
            ),
        ]
        path = csv_file(self.artifacts, "effects", rows)
        _, _, tracked = self.upload_path(self.admin, path, tag="effects")
        preview = dry = confirm = None
        summary: dict[str, Any] = {}
        if tracked:
            preview = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{tracked['public_id']}/preview",
                json={},
            )
            dry = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{tracked['public_id']}/dry-run",
            )
            dry_payload = response_json(dry) or {}
            summary = dry_payload.get("summary") or {}
            token = dry_payload.get("confirmation_token")
            if isinstance(token, str):
                confirm = self.runner.request(
                    self.admin,
                    "POST",
                    f"/api/historical-imports/{tracked['public_id']}/confirm",
                    json={"confirmation_token": token},
                )
        records = self.records(tracked["public_id"]) if tracked else []
        return_record = next(
            (item for item in records if item["record_type"] == "return"), {}
        )
        cancellation = next(
            (
                item
                for item in records
                if item["record_type"] == "cancellation"
                and item["record_status"] == "issued"
            ),
            {},
        )
        voided = next(
            (
                item
                for item in records
                if item["record_type"] == "cancellation"
                and item["record_status"] == "voided"
            ),
            {},
        )
        self.runner.check(
            "Cancelación activa resta demanda y conserva relación",
            preview is not None
            and preview.status_code == 200
            and dry is not None
            and dry.status_code == 200
            and cancellation.get("related_record_id") is not None
            and cancellation.get("include_in_demand") is True
            and summary.get("quantities", {}).get("cancellations") == "3.00",
            base=20,
            expected="cancellation=3.00 activa y relacionada",
            actual=summary.get("quantities"),
            cause="Relaciones/dry run no descuentan cancelaciones activas.",
            priority="P0",
        )
        self.runner.check(
            "Devolución activa resta demanda y conserva relación",
            return_record.get("related_record_id") is not None
            and return_record.get("include_in_demand") is True
            and summary.get("quantities", {}).get("returns") == "2.00",
            base=21,
            expected="return=2.00 activa y relacionada",
            actual=summary.get("quantities"),
            cause="Relaciones/dry run no descuentan devoluciones activas.",
            priority="P0",
        )
        self.runner.check(
            "Cancellation voided no activa demanda",
            voided.get("include_in_demand") is False
            and summary.get("quantities", {}).get("cancellations") == "3.00"
            and confirm is not None
            and confirm.status_code == 200,
            expected="voided excluida",
            actual=voided.get("include_in_demand"),
            cause="confirm_import incluye estados voided/cancelled en demanda.",
            priority="P0",
        )

        exceed_doc = f"DOC-{RUN_SHORT}-EXCEED"
        exceed_path = csv_file(
            self.artifacts,
            "return_exceeds_sale",
            [
                self.row(
                    quantity="1",
                    document_number=exceed_doc,
                    source_line_id="S",
                ),
                self.row(
                    quantity="2",
                    record_type="return",
                    document_number=exceed_doc,
                    source_line_id="R",
                ),
            ],
        )
        _, _, exceed = self.upload_path(
            self.admin, exceed_path, tag="return_exceeds_sale"
        )
        if exceed:
            self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{exceed['public_id']}/preview",
                json={},
            )
        exceed_errors = self.errors(exceed["public_id"]) if exceed else []
        exceed_dry = (
            self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{exceed['public_id']}/dry-run",
            )
            if exceed
            else None
        )
        if exceed_dry:
            self.runner.remember_error(exceed_dry)
        self.runner.check(
            "Return mayor que sale genera error y bloquea dry run",
            any(
                issue["error_code"] == "related_quantity_exceeded"
                and issue["severity"] == SEVERITY_ERROR
                for issue in exceed_errors
            )
            and exceed_dry is not None
            and exceed_dry.status_code == 422,
            expected="related_quantity_exceeded + 422",
            actual=(
                [issue["error_code"] for issue in exceed_errors],
                exceed_dry.status_code if exceed_dry else None,
            ),
            cause="_rebuild_dynamic_issues no suma devoluciones/cancelaciones contra la venta.",
            priority="P0",
        )
        return {"tracked": tracked}

    def correction_checks(self) -> None:
        assert self.admin
        document = f"DOC-{RUN_SHORT}-CORRECTION"
        path = csv_file(
            self.artifacts,
            "correction",
            [
                self.row(
                    quantity="5",
                    document_number=document,
                    source_line_id="S",
                ),
                self.row(
                    quantity="7",
                    record_type="correction",
                    document_number=document,
                    source_line_id="C",
                ),
            ],
        )
        _, _, tracked = self.upload_path(self.admin, path, tag="correction")
        summary: dict[str, Any] = {}
        confirm = None
        if tracked:
            self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{tracked['public_id']}/preview",
                json={},
            )
            dry = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{tracked['public_id']}/dry-run",
            )
            payload = response_json(dry) or {}
            summary = payload.get("summary") or {}
            if isinstance(payload.get("confirmation_token"), str):
                confirm = self.runner.request(
                    self.admin,
                    "POST",
                    f"/api/historical-imports/{tracked['public_id']}/confirm",
                    json={"confirmation_token": payload["confirmation_token"]},
                )
        records = self.records(tracked["public_id"]) if tracked else []
        sale = next((item for item in records if item["record_type"] == "sale"), {})
        correction = next(
            (item for item in records if item["record_type"] == "correction"), {}
        )
        self.runner.check(
            "Correction supersede el hecho vigente sin borrar trazabilidad",
            confirm is not None
            and confirm.status_code == 200
            and sale.get("effective_status") == "superseded"
            and sale.get("include_in_demand") is False
            and sale.get("superseded_by_record_id") == correction.get("id")
            and correction.get("include_in_demand") is True
            and correction.get("related_record_id") == sale.get("id")
            and summary.get("quantities", {}).get("corrections") == "7.00"
            and summary.get("quantities", {}).get("sales") == "0.00",
            expected="venta superseded; correction 7 vigente",
            actual=(sale, correction, summary.get("quantities")),
            cause="confirm_import no aplica supersede lógico de correcciones.",
            priority="P0",
        )

        # Cadena entre lotes: el lote fuente no puede revertirse mientras una
        # corrección confirmada dependa de él. Al revertir la corrección se
        # restaura el hecho previo sin borrar ninguna de las dos filas.
        cross_document = f"DOC-{RUN_SHORT}-CROSS-REVERT"
        source_path = csv_file(
            self.artifacts,
            "correction_source_batch",
            [
                self.row(
                    quantity="5",
                    document_number=cross_document,
                    source_record_id=f"REC-{RUN_SHORT}-CROSS-SOURCE",
                    source_line_id="S",
                )
            ],
        )
        _, _, source_batch = self.upload_path(
            self.admin, source_path, tag="correction_source_batch"
        )
        source_confirm = None
        if source_batch:
            self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{source_batch['public_id']}/preview",
                json={},
            )
            source_dry = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{source_batch['public_id']}/dry-run",
            )
            source_token = (response_json(source_dry) or {}).get(
                "confirmation_token"
            )
            if isinstance(source_token, str):
                source_confirm = self.runner.request(
                    self.admin,
                    "POST",
                    f"/api/historical-imports/{source_batch['public_id']}/confirm",
                    json={"confirmation_token": source_token},
                )

        correction_path = csv_file(
            self.artifacts,
            "correction_dependent_batch",
            [
                self.row(
                    quantity="7",
                    record_type="correction",
                    document_number=cross_document,
                    source_record_id=f"REC-{RUN_SHORT}-CROSS-CORRECTION",
                    source_line_id="C",
                )
            ],
        )
        _, _, correction_batch = self.upload_path(
            self.admin, correction_path, tag="correction_dependent_batch"
        )
        correction_confirm = blocked_source_revert = correction_revert = None
        final_source_revert = None
        if correction_batch:
            self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{correction_batch['public_id']}/preview",
                json={},
            )
            correction_dry = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{correction_batch['public_id']}/dry-run",
            )
            correction_token = (response_json(correction_dry) or {}).get(
                "confirmation_token"
            )
            if isinstance(correction_token, str):
                correction_confirm = self.runner.request(
                    self.admin,
                    "POST",
                    f"/api/historical-imports/{correction_batch['public_id']}/confirm",
                    json={"confirmation_token": correction_token},
                )
        if source_batch and correction_confirm is not None:
            blocked_source_revert = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{source_batch['public_id']}/revert",
                json={"reason": "Debe bloquearse por dependencia"},
            )
            self.runner.remember_error(blocked_source_revert)
        if correction_batch and correction_confirm is not None:
            correction_revert = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{correction_batch['public_id']}/revert",
                json={"reason": "Restaurar versión histórica anterior"},
            )

        source_rows_after_restore = (
            self.records(source_batch["public_id"]) if source_batch else []
        )
        correction_rows_after_revert = (
            self.records(correction_batch["public_id"])
            if correction_batch
            else []
        )
        if source_batch and correction_revert is not None:
            final_source_revert = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{source_batch['public_id']}/revert",
                json={"reason": "Dependencia ya revertida"},
            )
        self.runner.check(
            "Revertir corrección entre lotes restaura origen y respeta dependencias",
            source_confirm is not None
            and source_confirm.status_code == 200
            and correction_confirm is not None
            and correction_confirm.status_code == 200
            and blocked_source_revert is not None
            and blocked_source_revert.status_code == 409
            and correction_revert is not None
            and correction_revert.status_code == 200
            and len(source_rows_after_restore) == 1
            and source_rows_after_restore[0]["effective_status"] == "issued"
            and source_rows_after_restore[0]["include_in_demand"] is True
            and source_rows_after_restore[0]["superseded_by_record_id"] is None
            and len(correction_rows_after_revert) == 1
            and correction_rows_after_revert[0]["include_in_demand"] is False
            and final_source_revert is not None
            and final_source_revert.status_code == 200,
            expected="409 en origen dependiente; restauración; luego revert 200",
            actual=(
                source_confirm.status_code if source_confirm else None,
                correction_confirm.status_code if correction_confirm else None,
                blocked_source_revert.status_code if blocked_source_revert else None,
                correction_revert.status_code if correction_revert else None,
                final_source_revert.status_code if final_source_revert else None,
            ),
            cause="revert_import no restaura o no protege la cadena de correcciones.",
            priority="P0",
        )

        negative_doc = f"DOC-{RUN_SHORT}-NEGNET"
        negative_path = csv_file(
            self.artifacts,
            "negative_net",
            [
                self.row(
                    quantity="10",
                    document_number=negative_doc,
                    source_line_id="S",
                ),
                self.row(
                    quantity="8",
                    record_type="return",
                    document_number=negative_doc,
                    source_line_id="R",
                ),
                self.row(
                    quantity="5",
                    record_type="correction",
                    document_number=negative_doc,
                    source_line_id="C",
                ),
            ],
        )
        _, _, negative = self.upload_path(
            self.admin, negative_path, tag="negative_net"
        )
        blocked = review = dry = None
        if negative:
            self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{negative['public_id']}/preview",
                json={},
            )
            issues = self.errors(negative["public_id"])
            negative_issue = next(
                (
                    issue
                    for issue in issues
                    if issue["error_code"] == "negative_net_demand"
                ),
                None,
            )
            blocked = self.runner.request(
                self.admin,
                "POST",
                f"/api/historical-imports/{negative['public_id']}/dry-run",
            )
            correction_row = next(
                (
                    item
                    for item in self.records(negative["public_id"])
                    if item["record_type"] == "correction"
                ),
                None,
            )
            if negative_issue and correction_row:
                review = self.runner.request(
                    self.admin,
                    "POST",
                    (
                        f"/api/historical-imports/{negative['public_id']}"
                        f"/records/{correction_row['id']}/review"
                    ),
                    json={"approve": ["negative_net"]},
                )
                dry = self.runner.request(
                    self.admin,
                    "POST",
                    f"/api/historical-imports/{negative['public_id']}/dry-run",
                )
        dry_payload = response_json(dry) if dry is not None else {}
        self.runner.check(
            "Neto negativo exige review admin explícito antes del dry run",
            blocked is not None
            and blocked.status_code == 422
            and review is not None
            and review.status_code == 200
            and dry is not None
            and dry.status_code == 200
            and (dry_payload or {}).get("summary", {})
            .get("quantities", {})
            .get("net_demand")
            == "-3.00",
            expected="422 -> review 200 -> neto -3.00",
            actual=(
                blocked.status_code if blocked else None,
                review.status_code if review else None,
                dry.status_code if dry else None,
            ),
            cause="negative_net_demand no se marca como review bloqueante.",
            priority="P0",
        )

    def warning_blocker_and_rollback_checks(self) -> dict[str, Any]:
        assert self.admin and self.active_product and self.admin_user_id
        path = csv_file(
            self.artifacts,
            "warning_rollback",
            [
                self.row(
                    name=f"Nombre histórico distinto {RUN_SHORT}",
                    document_number=f"DOC-{RUN_SHORT}-WARNING",
                    source_line_id="1",
                )
            ],
        )
        _, _, tracked = self.upload_path(
            self.admin, path, tag="warning_rollback"
        )
        if not tracked:
            self.runner.check(
                "Warning permite y rollback por error preserva estado",
                False,
                base=24,
                expected="lote creado",
                actual=False,
                cause="No se pudo preparar el lote de rollback.",
                priority="P0",
            )
            return {}
        self.runner.request(
            self.admin,
            "POST",
            f"/api/historical-imports/{tracked['public_id']}/preview",
            json={},
        )
        errors = self.errors(tracked["public_id"])
        first_dry = self.runner.request(
            self.admin,
            "POST",
            f"/api/historical-imports/{tracked['public_id']}/dry-run",
        )
        first_token = (response_json(first_dry) or {}).get("confirmation_token")
        self.runner.check(
            "Warning no bloquea dry run ni confirmación potencial",
            first_dry.status_code == 200
            and isinstance(first_token, str)
            and any(
                issue["error_code"] == "product_name_mismatch"
                and issue["severity"] == SEVERITY_WARNING
                and issue["resolution_status"] == RESOLUTION_NOT_REQUIRED
                for issue in errors
            ),
            expected="warning + dry run 200",
            actual=(first_dry.status_code, [item["error_code"] for item in errors]),
            cause="_blocking_issue_count trata warning como bloqueante.",
            priority="P0",
        )

        injected = [
            self.direct_insert_issue(
                tracked["public_id"],
                code="controlled_blocking_error",
                severity=SEVERITY_ERROR,
                message="Error controlado de la suite.",
            ),
            self.direct_insert_issue(
                tracked["public_id"],
                code="controlled_blocking_review",
                severity=SEVERITY_REVIEW,
                message="Revisión controlada de la suite.",
            ),
        ]
        blocked_confirm = self.runner.request(
            self.admin,
            "POST",
            f"/api/historical-imports/{tracked['public_id']}/confirm",
            json={"confirmation_token": first_token},
        )
        self.runner.remember_error(blocked_confirm)
        state_blocked = self.import_state(tracked["public_id"])
        self.runner.check(
            "Error y review unresolved bloquean confirmación aunque exista token",
            blocked_confirm.status_code == 422
            and state_blocked["status"] == "previewed"
            and state_blocked["confirmation_token_hash"] is None,
            expected="422 y token invalidado",
            actual=(blocked_confirm.status_code, state_blocked["status"]),
            cause="confirm_import no recalcula blockers justo antes del commit.",
            priority="P0",
        )
        self.delete_issues(injected)

        second_dry = self.runner.request(
            self.admin,
            "POST",
            f"/api/historical-imports/{tracked['public_id']}/dry-run",
        )
        second_token = (response_json(second_dry) or {}).get("confirmation_token")
        before_state = self.import_state(tracked["public_id"])
        before_records = stable_hash(self.records(tracked["public_id"]))

        previous_disabled = self.app.logger.disabled
        self.app.logger.disabled = True
        try:
            with mock.patch(
                "sqlalchemy.orm.session.Session.commit",
                side_effect=RuntimeError("controlled-confirmation-failure"),
            ):
                response_500 = self.runner.request(
                    self.admin,
                    "POST",
                    f"/api/historical-imports/{tracked['public_id']}/confirm",
                    json={"confirmation_token": second_token},
                )
        finally:
            self.app.logger.disabled = previous_disabled
        self.runner.remember_error(response_500)
        after_state = self.import_state(tracked["public_id"])
        after_records = stable_hash(self.records(tracked["public_id"]))
        self.runner.check(
            "Error interno durante commit revierte toda transición y devuelve 500 seguro",
            response_500.status_code == 500
            and safe_error_json(response_500, 500)
            and before_state["status"] == after_state["status"] == "dry_run_ready"
            and before_state["confirmation_token_hash"]
            == after_state["confirmation_token_hash"]
            and before_records == after_records,
            base=24,
            expected="500 JSON y rollback completo",
            actual=(response_500.status_code, after_state["status"]),
            cause="confirm_import no ejecuta rollback tras una excepción de commit.",
            priority="P0",
        )

        normal_confirm = self.runner.request(
            self.admin,
            "POST",
            f"/api/historical-imports/{tracked['public_id']}/confirm",
            json={"confirmation_token": second_token},
        )
        self.runner.check(
            "Tras rollback, el mismo token confirma una sola vez",
            normal_confirm.status_code == 200
            and (response_json(normal_confirm) or {}).get("idempotent_replay") is False,
            expected="HTTP 200 transición real",
            actual=normal_confirm.status_code,
            cause="El rollback dejó sesión/token en estado inconsistente.",
            priority="P0",
        )
        return {"tracked": tracked, "response_500": response_500}

    def injection_and_privacy_checks(self, public_id: str) -> None:
        assert self.admin and self.inventory
        issue_id = self.direct_insert_issue(
            public_id,
            code="+FORMULA_CODE",
            severity=SEVERITY_WARNING,
            message="=CMD|' /C calc'!A0",
            field="@FORMULA_FIELD",
            resolution_status=RESOLUTION_NOT_REQUIRED,
        )
        admin_export = self.runner.request(
            self.admin,
            "GET",
            f"/api/historical-imports/{public_id}/errors.csv",
        )
        inventory_export = self.runner.request(
            self.inventory,
            "GET",
            f"/api/historical-imports/{public_id}/errors.csv",
        )
        decoded = admin_export.content.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(decoded), delimiter=";"))
        dangerous_export_rows = [
            row
            for row in rows[1:]
            if any("FORMULA" in cell or "CMD" in cell for cell in row)
        ]
        neutralized = bool(dangerous_export_rows) and all(
            not cell.lstrip().startswith(("=", "+", "-", "@"))
            for row in dangerous_export_rows
            for cell in row
        )
        self.runner.check(
            "Export errors neutraliza CSV injection para admin e inventario",
            admin_export.status_code == 200
            and inventory_export.status_code == 200
            and neutralized,
            expected="celdas peligrosas prefijadas con apóstrofo",
            actual=(admin_export.status_code, inventory_export.status_code, neutralized),
            cause="_neutralize_csv_cell/errors_csv_stream no neutraliza fórmulas.",
            priority="P0",
        )
        self.delete_issues([issue_id])

        state = self.import_state(public_id)
        assert state is not None
        storage_key = state["storage_key"]
        admin_detail = self.runner.request(
            self.admin, "GET", f"/api/historical-imports/{public_id}"
        )
        inventory_detail = self.runner.request(
            self.inventory, "GET", f"/api/historical-imports/{public_id}"
        )
        admin_payload = response_json(admin_detail) or {}
        inventory_payload = response_json(inventory_detail) or {}
        private_attempts = [
            self.runner.request(
                self.admin, "GET", f"/media/historical_imports/{storage_key}"
            ),
            self.runner.request(
                self.admin, "GET", f"/static/historical_imports/{storage_key}"
            ),
            self.runner.request(
                self.admin, "GET", f"/instance/historical_imports/{storage_key}"
            ),
            self.runner.request(
                self.admin, "GET", f"/historical_imports/{storage_key}"
            ),
        ]
        serialized_admin = json.dumps(admin_payload, ensure_ascii=False)
        serialized_inventory = json.dumps(inventory_payload, ensure_ascii=False)
        self.runner.check(
            "Archivo privado no es accesible por web/media/static",
            all(response.status_code == 404 for response in private_attempts)
            and all(response.content != self.private_path(storage_key).read_bytes() for response in private_attempts),
            expected="cuatro 404 sin bytes privados",
            actual=[response.status_code for response in private_attempts],
            cause="Una ruta pública expone instance/historical_imports.",
            priority="P0",
        )
        self.runner.check(
            "storage_key nunca se serializa; inventario no ve SHA completo ni filename",
            "storage_key" not in serialized_admin
            and storage_key not in serialized_admin
            and "admin_metadata" in admin_payload
            and state["sha256"] in serialized_admin
            and state["sha256"] not in serialized_inventory
            and state["storage_key"] not in serialized_inventory
            and state["sha256"] not in serialized_inventory
            and self.valid_original_filename not in serialized_inventory
            and "admin_metadata" not in inventory_payload,
            expected="redacción por rol",
            actual=(
                "admin_metadata" in admin_payload,
                "admin_metadata" in inventory_payload,
            ),
            cause="serialize_import expone metadata administrativa a inventario.",
            priority="P0",
        )

    def pagination_and_permissions_checks(
        self,
        public_id: str,
        record_id: int,
        records_public_id: str,
        errors_public_id: str,
    ) -> None:
        assert self.admin and self.inventory and self.seller
        admin_list = self.runner.request(
            self.admin, "GET", "/api/historical-imports?page=1&per_page=1"
        )
        inventory_list = self.runner.request(
            self.inventory, "GET", "/api/historical-imports?page=1&per_page=1"
        )
        records_page = self.runner.request(
            self.admin,
            "GET",
            f"/api/historical-imports/{records_public_id}/records?page=1&per_page=2",
        )
        errors_page = self.runner.request(
            self.admin,
            "GET",
            f"/api/historical-imports/{errors_public_id}/errors?page=1&per_page=1",
        )
        invalid_page = self.runner.request(
            self.admin, "GET", "/api/historical-imports?page=0&per_page=101"
        )
        self.runner.remember_error(invalid_page)
        list_payload = response_json(admin_list) or {}
        inv_payload = response_json(inventory_list) or {}
        records_payload = response_json(records_page) or {}
        errors_payload = response_json(errors_page) or {}
        self.runner.check(
            "List/detail/records/errors paginan y validan límites",
            admin_list.status_code == 200
            and len(list_payload.get("items", [])) <= 1
            and list_payload.get("pagination", {}).get("per_page") == 1
            and records_page.status_code == 200
            and len(records_payload.get("items", [])) <= 2
            and errors_page.status_code == 200
            and len(errors_payload.get("items", [])) <= 1
            and invalid_page.status_code == 400,
            expected="paginación consistente + 400 inválido",
            actual=(
                admin_list.status_code,
                records_page.status_code,
                errors_page.status_code,
                invalid_page.status_code,
            ),
            cause="_parse_pagination/list_* no aplica page/per_page consistentemente.",
            priority="P1",
        )
        self.runner.check(
            "Listado inventario está redactado por rol",
            inventory_list.status_code == 200
            and all(
                "admin_metadata" not in item
                for item in inv_payload.get("items", [])
            ),
            expected="sin admin_metadata",
            actual=inv_payload.get("items", []),
            cause="serialize_import(is_admin=False) filtra metadata de forma incompleta.",
            priority="P0",
        )

        matrix = [
            ("GET", "", None, True),
            ("POST", "/upload", None, False),
            ("GET", "/template.csv", None, True),
            ("GET", f"/{public_id}", None, True),
            ("GET", f"/{public_id}/records", None, True),
            ("GET", f"/{public_id}/errors", None, True),
            ("POST", f"/{public_id}/preview", {}, False),
            ("POST", f"/{public_id}/dry-run", None, False),
            ("POST", f"/{public_id}/confirm", {"confirmation_token": "x"}, False),
            ("POST", f"/{public_id}/revert", {"reason": "x"}, False),
            (
                "POST",
                f"/{public_id}/records/{record_id}/review",
                {"approve": []},
                False,
            ),
            (
                "GET",
                f"/{public_id}/records/{record_id}/relationship-candidates",
                None,
                False,
            ),
            ("GET", f"/{public_id}/errors.csv", None, True),
        ]
        inventory_statuses = []
        seller_statuses = []
        anonymous_statuses = []
        for method, suffix, body, inventory_allowed in matrix:
            kwargs = {"json": body} if body is not None else {}
            inv = self.runner.request(
                self.inventory,
                method,
                f"/api/historical-imports{suffix}",
                **kwargs,
            )
            seller = self.runner.request(
                self.seller,
                method,
                f"/api/historical-imports{suffix}",
                **kwargs,
            )
            anonymous = self.runner.request(
                None,
                method,
                f"/api/historical-imports{suffix}",
                **kwargs,
            )
            inventory_statuses.append((inv.status_code, inventory_allowed))
            seller_statuses.append(seller.status_code)
            anonymous_statuses.append(anonymous.status_code)
            if inv.status_code in {400, 401, 403, 404, 409, 413, 422, 500}:
                self.runner.remember_error(inv)
            self.runner.remember_error(seller)
            self.runner.remember_error(anonymous)
        self.runner.check(
            "Permisos por endpoint: inventario read/export; writes 403",
            all(
                status == (200 if allowed else 403)
                for status, allowed in inventory_statuses
            ),
            expected="200 read/export; 403 writes",
            actual=inventory_statuses,
            cause="ROLE_PERMISSIONS/rutas históricas no aplican permisos granulares.",
            priority="P0",
        )
        self.runner.check(
            "Permisos por endpoint: vendedor 403 en todo el módulo",
            all(status == 403 for status in seller_statuses),
            expected=f"{len(matrix)} respuestas 403",
            actual=seller_statuses,
            cause="ROLE_VENDEDOR recibió permisos históricos no autorizados.",
            priority="P0",
        )
        self.runner.check(
            "Permisos por endpoint: sin sesión 401 JSON",
            all(status == 401 for status in anonymous_statuses),
            expected=f"{len(matrix)} respuestas 401",
            actual=anonymous_statuses,
            cause="Decoradores históricos no exigen autenticación antes del contrato.",
            priority="P0",
        )

    def operational_and_regression_checks(self) -> None:
        assert self.admin
        current = self.db_snapshot(OPERATIONAL_TABLES)
        self.runner.check(
            "current_stock/minimum_stock/is_active y demás campos no cambian",
            current["products"] == self.initial_operational["products"],
            base=26,
            expected=self.initial_operational["products"]["rows_hash"],
            actual=current["products"]["rows_hash"],
            cause="El importador histórico modificó products/current_stock.",
            priority="P0",
        )
        self.runner.check(
            "stock_movements permanece idéntica",
            current["stock_movements"]
            == self.initial_operational["stock_movements"],
            base=27,
            expected=self.initial_operational["stock_movements"]["rows_hash"],
            actual=current["stock_movements"]["rows_hash"],
            cause="El importador histórico generó movimientos operativos.",
            priority="P0",
        )
        notes_unchanged = (
            current["delivery_notes"]
            == self.initial_operational["delivery_notes"]
            and current["delivery_note_items"]
            == self.initial_operational["delivery_note_items"]
        )
        self.runner.check(
            "delivery_notes y delivery_note_items permanecen idénticas",
            notes_unchanged,
            base=28,
            expected="hashes de notas e ítems idénticos",
            actual=notes_unchanged,
            cause="El importador histórico escribió notas de entrega.",
            priority="P0",
        )

        current_api = self.api_snapshot(self.admin)
        pages = {
            "/dashboard": self.runner.request(self.admin, "GET", "/dashboard"),
            "/products": self.runner.request(self.admin, "GET", "/products"),
            "/categories": self.runner.request(self.admin, "GET", "/categories"),
            "/inventory": self.runner.request(self.admin, "GET", "/inventory"),
            "/delivery-notes": self.runner.request(
                self.admin, "GET", "/delivery-notes"
            ),
        }
        self.runner.check(
            "Regresión: KPIs/endpoints JSON y páginas operativas no cambian",
            current_api == self.initial_api
            and all(response.status_code == 200 for response in pages.values())
            and all(
                "text/html" in response.headers.get("Content-Type", "")
                for response in pages.values()
            ),
            base=31,
            expected="snapshots API iguales y páginas 200 HTML",
            actual=(
                current_api == self.initial_api,
                {path: response.status_code for path, response in pages.items()},
            ),
            cause="El módulo histórico introdujo una regresión operativa/reportes.",
            priority="P0",
        )

    def error_contract_checks(self) -> None:
        assert self.admin
        not_found = self.runner.request(
            self.admin,
            "GET",
            "/api/historical-imports/00000000-0000-0000-0000-000000000000",
        )
        self.runner.remember_error(not_found)
        bad_json = self.runner.request(
            self.admin,
            "POST",
            "/api/historical-imports/00000000-0000-0000-0000-000000000000/confirm",
            data="[]",
            headers={"Content-Type": "application/json"},
        )
        self.runner.remember_error(bad_json)
        required = {400, 401, 403, 404, 409, 413, 422, 500}
        missing = required - set(self.runner.safe_error_responses)
        self.runner.check(
            "Se ejercitaron errores 400/401/403/404/409/413/422/500",
            not missing,
            expected=sorted(required),
            actual=sorted(self.runner.safe_error_responses),
            cause="La suite no alcanzó uno o más contratos de error requeridos.",
            priority="P1",
        )
        for status in sorted(required):
            response = self.runner.safe_error_responses.get(status)
            self.runner.check(
                f"Error HTTP {status} es JSON seguro sin detalles sensibles",
                response is not None and safe_error_json(response, status),
                expected=f"{status} {{error: mensaje seguro}}",
                actual=(
                    getattr(response, "status_code", None),
                    response_content_type(response) if response else None,
                ),
                cause="Manejador global/blueprint filtra stack, ruta privada o respuesta HTML.",
                priority="P0",
            )

    def cleanup_historical(self) -> None:
        with self.context():
            discovered = (
                HistoricalImport.query.filter(
                    HistoricalImport.source_system.like(f"{SOURCE_PREFIX}%")
                )
                .order_by(HistoricalImport.id)
                .all()
            )
            imports = [
                item
                for item in discovered
                if int(item.id) not in self.initial_historical_ids
            ]
            import_ids = [int(item.id) for item in imports]
            for item in imports:
                self.artifacts.created_import_ids.add(int(item.id))
                self.artifacts.created_public_ids.add(item.public_id)
                self.artifacts.tracked_storage_keys.add(item.storage_key)
            if import_ids:
                # Exclusivamente en la SQLite temporal: se desarma el guard de
                # lote confirmado/revertido para poder retirar fixtures propias.
                HistoricalImport.query.filter(
                    HistoricalImport.id.in_(import_ids)
                ).update(
                    {HistoricalImport.status: "uploaded"},
                    synchronize_session=False,
                )
                db.session.flush()
                record_ids = [
                    value
                    for (value,) in db.session.query(HistoricalDemandRecord.id)
                    .filter(
                        HistoricalDemandRecord.historical_import_id.in_(import_ids)
                    )
                    .all()
                ]
                import_count = len(import_ids)
                record_count = len(record_ids)
                error_count = (
                    HistoricalImportError.query.filter(
                        HistoricalImportError.historical_import_id.in_(import_ids)
                    ).count()
                )
                self.artifacts.created_historical_rows.update(
                    {
                        "historical_imports": import_count,
                        "historical_demand_records": record_count,
                        "historical_import_errors": error_count,
                    }
                )
                if record_ids:
                    HistoricalDemandRecord.query.filter(
                        HistoricalDemandRecord.id.in_(record_ids)
                    ).update(
                        {
                            HistoricalDemandRecord.related_record_id: None,
                            HistoricalDemandRecord.superseded_by_record_id: None,
                            HistoricalDemandRecord.superseded_by_import_id: None,
                        },
                        synchronize_session=False,
                    )
                deleted_errors = HistoricalImportError.query.filter(
                    HistoricalImportError.historical_import_id.in_(import_ids)
                ).delete(synchronize_session=False)
                deleted_records = HistoricalDemandRecord.query.filter(
                    HistoricalDemandRecord.historical_import_id.in_(import_ids)
                ).delete(synchronize_session=False)
                deleted_imports = HistoricalImport.query.filter(
                    HistoricalImport.id.in_(import_ids)
                ).delete(synchronize_session=False)
                db.session.commit()
                self.artifacts.deleted_historical_rows.update(
                    {
                        "historical_imports": int(deleted_imports),
                        "historical_demand_records": int(deleted_records),
                        "historical_import_errors": int(deleted_errors),
                    }
                )

        private_root = (
            Path(self.app.instance_path)
            / historical_import_service.PRIVATE_STORAGE_DIR
        )
        for storage_key in sorted(self.artifacts.tracked_storage_keys):
            path = private_root / storage_key
            if path.exists() and storage_key not in self.initial_private_names:
                path.unlink()
                self.artifacts.deleted_private_files += 1

    def final_integrity_checks(self) -> None:
        final_operational = self.db_snapshot(OPERATIONAL_TABLES)
        final_historical = self.db_snapshot(HISTORICAL_TABLES)
        final_private = self.private_snapshot()
        final_api = self.api_snapshot(self.admin) if self.admin else {}

        no_temp_files = (
            self.artifacts.root.exists()
            and all(not path.exists() for path in self.artifacts.created_temp_files)
            and self.artifacts.deleted_temp_files
            == len(self.artifacts.created_temp_files)
        )
        self.runner.check(
            "Sin CSV/pyc temporales ni archivos privados residuales",
            no_temp_files and final_private == self.initial_private,
            base=32,
            expected=(
                len(self.artifacts.created_temp_files),
                self.initial_private["files_hash"],
            ),
            actual=(
                self.artifacts.deleted_temp_files,
                final_private["files_hash"],
            ),
            cause="Cleanup finally no eliminó un archivo creado por la suite.",
            priority="P0",
        )
        all_snapshots_equal = (
            final_operational == self.initial_operational
            and final_historical == self.initial_historical
            and final_private == self.initial_private
            and final_api == self.initial_api
        )
        self.runner.check(
            "Conteos, IDs, valores, KPIs e históricos vuelven al snapshot inicial",
            all_snapshots_equal,
            base=33,
            expected="snapshots before/after idénticos",
            actual={
                "operational": final_operational == self.initial_operational,
                "historical": final_historical == self.initial_historical,
                "private": final_private == self.initial_private,
                "api": final_api == self.initial_api,
            },
            cause="La suite o el módulo dejó DML/archivo fuera de las tres tablas históricas.",
            priority="P0",
        )
        self.runner.check(
            "Las 34 pruebas base fueron numeradas y ejecutadas",
            self.runner.base_seen == set(range(1, 35)),
            expected=list(range(1, 35)),
            actual=sorted(self.runner.base_seen),
            cause="Falta asociar una cobertura obligatoria al número base correspondiente.",
            priority="P1",
        )
        self.print_snapshot("SNAPSHOT FINAL OPERATIVO", final_operational)
        self.print_snapshot("SNAPSHOT FINAL HISTÓRICO", final_historical)
        print(
            "private_files: "
            f"count={final_private['count']} hash={final_private['files_hash']}"
        )
        print("--- SNAPSHOT FINAL API ---")
        for path, value in final_api.items():
            print(
                f"{path}: status={value['status']} hash={value['payload_hash']}"
            )

    def execute(self) -> int:
        self.initialize_isolated_database()
        self.runner.check(
            "Runtime fail-closed usa SQLite e instance_path bajo tempfile",
            self.artifacts.database_path.exists()
            and Path(self.app.instance_path).resolve()
            == self.artifacts.instance_path.resolve(),
            expected="DB e instance_path temporales",
            actual=(
                self.artifacts.database_path.exists(),
                Path(self.app.instance_path).resolve()
                == self.artifacts.instance_path.resolve(),
            ),
            cause="La factory aislada no quedó confinada al tempfile.",
            priority="P0",
        )
        self.ensure_server()
        self.inspect_prerequisites()
        if self.active_product is None or self.admin_user_id is None:
            raise RuntimeError("Prerrequisitos de producto/admin no disponibles")

        self.initial_operational = self.db_snapshot(OPERATIONAL_TABLES)
        self.initial_historical = self.db_snapshot(HISTORICAL_TABLES)
        self.initial_private = self.private_snapshot()
        self.initial_private_names = set(self.initial_private["files"])
        self.print_snapshot("SNAPSHOT INICIAL OPERATIVO", self.initial_operational)
        self.print_snapshot("SNAPSHOT INICIAL HISTÓRICO", self.initial_historical)
        print(
            "private_files: "
            f"count={self.initial_private['count']} "
            f"hash={self.initial_private['files_hash']}"
        )

        self.run_migration_idempotence()
        self.static_contract_checks()
        self.access_checks_before_data()

        admin_identifier, admin_password = self.credentials[ROLE_ADMIN]
        inventory_identifier, inventory_password = self.credentials[ROLE_INVENTARIO]
        seller_identifier, seller_password = self.credentials[ROLE_VENDEDOR]
        self.admin = self.login(admin_identifier, admin_password, "admin efímero")
        self.inventory = self.login(
            inventory_identifier, inventory_password, "inventario efímero"
        )
        self.seller = self.login(
            seller_identifier, seller_password, "vendedor efímero"
        )
        self.initial_api = self.api_snapshot(self.admin)
        print("--- SNAPSHOT INICIAL API ---")
        for path, value in self.initial_api.items():
            print(
                f"{path}: status={value['status']} hash={value['payload_hash']}"
            )

        self.csrf_contract_checks()
        self.page_role_checks()
        self.template_check()
        self.invalid_upload_checks()
        invalid_rows = self.row_validation_checks()
        mapping = self.limit_and_mapping_checks()
        self.pure_helper_checks()
        valid = self.valid_lifecycle_checks()
        self.expired_token_check()
        matching = self.matching_review_checks()
        self.ambiguity_and_duplicate_checks()
        self.inactive_product_check()
        self.weak_fingerprint_checks()
        effects = self.effects_checks()
        self.correction_checks()
        rollback = self.warning_blocker_and_rollback_checks()

        valid_tracked = valid["tracked"]
        self.injection_and_privacy_checks(valid_tracked["public_id"])
        valid_records = self.records(valid_tracked["public_id"])
        self.pagination_and_permissions_checks(
            valid_tracked["public_id"],
            int(valid_records[0]["id"]),
            effects["tracked"]["public_id"],
            invalid_rows["tracked"]["public_id"],
        )
        self.operational_and_regression_checks()
        self.error_contract_checks()

        # Referencias para evitar que una optimización futura elimine cobertura.
        self.runner.check(
            "Lotes auxiliares de mapping/matching/rollback fueron ejercitados",
            all(
                item and item.get("tracked")
                for item in (
                    {"tracked": mapping.get("tracked_40")},
                    matching,
                    rollback,
                )
            ),
            expected="tres lotes auxiliares",
            actual=True,
            cause="Una fase auxiliar no alcanzó su upload/preview.",
            priority="P1",
        )
        return 0


def print_report(runner: Runner, artifacts: ArtifactTracker) -> None:
    print("\n" + "=" * 78)
    print("TEST ENGINEER REPORT — IMPORTADOR HISTÓRICO CSV V1")
    print("=" * 78)
    print(
        f"Checks: total={runner.total} OK={runner.ok} "
        f"FALLO={runner.failed} SKIP={runner.skipped}"
    )
    print(f"Llamadas HTTP: {runner.http_calls}")
    print(
        "Temporales: "
        f"creados={len(artifacts.created_temp_files)} "
        f"eliminados={artifacts.deleted_temp_files}"
    )
    print(
        "Archivos privados: "
        f"identificados={len(artifacts.tracked_storage_keys)} "
        f"eliminados={artifacts.deleted_private_files}"
    )
    print(
        "Filas históricas creadas/eliminadas: "
        f"{artifacts.created_historical_rows} / "
        f"{artifacts.deleted_historical_rows}"
    )
    print("Tablas/fixtures operativas: solo dentro de SQLite temporal desechable")
    print("Base real, servidor externo y .env usados por la suite: 0")
    if runner.defects:
        print("\nDEFECTOS DETECTADOS (producción no modificada):")
        for defect in runner.defects:
            print(
                f"- #{defect.check_number:03d} [{defect.priority}] {defect.test}; "
                f"esperado={defect.expected}; real={defect.actual}; "
                f"causa probable={defect.probable_cause}"
            )
    else:
        print("\nDefectos detectados: 0")
    print(
        "Resultado: "
        + ("OK" if runner.failed == 0 else "FALLO")
        + f" — exit code previsto {0 if runner.failed == 0 else 1}"
    )


def main() -> int:
    runner = Runner()
    artifacts = ArtifactTracker()
    suite: HistoricalImportSuite | None = None
    fatal_error = False
    try:
        suite = HistoricalImportSuite(runner, artifacts)
        suite.execute()
    except Exception as exc:
        fatal_error = True
        runner.check(
            "La suite completó todas sus fases sin excepción no controlada",
            False,
            expected="ejecución completa",
            actual=type(exc).__name__,
            cause=(
                "Error de la suite aislada Flask/SQLite; "
                "los detalles sensibles se omitieron del log."
            ),
            priority="P0",
        )
    finally:
        if suite is not None:
            try:
                suite.cleanup_historical()
            except Exception as exc:
                runner.check(
                    "Cleanup histórico en finally",
                    False,
                    expected="cleanup completo",
                    actual=type(exc).__name__,
                    cause="Falló la eliminación FK-order de filas propias.",
                    priority="P0",
                )
        artifacts.cleanup_temp_files()
        if suite is not None:
            try:
                if suite.initial_operational:
                    suite.final_integrity_checks()
            except Exception as exc:
                runner.check(
                    "Verificación final de snapshots",
                    False,
                    expected="snapshots verificables",
                    actual=type(exc).__name__,
                    cause="No fue posible leer/validar los snapshots finales.",
                    priority="P0",
                )

        stopped = suite is None
        disposed = suite is None
        if suite is not None:
            try:
                suite.stop_server()
                stopped = True
            except Exception as exc:
                runner.check(
                    "Servidor aislado detenido antes de borrar tempfile",
                    False,
                    expected="shutdown completo",
                    actual=type(exc).__name__,
                    cause="Werkzeug no se detuvo dentro del plazo seguro.",
                    priority="P0",
                )
            try:
                suite.dispose_database()
                disposed = True
            except Exception as exc:
                runner.check(
                    "Engine SQLite dispuesto antes de borrar tempfile",
                    False,
                    expected="engine.dispose completo",
                    actual=type(exc).__name__,
                    cause="Quedó una conexión abierta a la SQLite temporal.",
                    priority="P0",
                )
        if stopped and disposed:
            artifacts.cleanup_root()

    print_report(runner, artifacts)
    return 1 if fatal_error or runner.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
