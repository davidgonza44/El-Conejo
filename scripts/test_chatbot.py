#!/usr/bin/env python3
"""Suite automatizada no mutante del chatbot.

Combina HTTP real, pruebas unitarias de helpers, aserciones estáticas y
snapshots completos de las tablas operativas. No crea fixtures, no llama
endpoints operativos de escritura y no confirma transacciones.

Uso:
    python scripts/test_chatbot.py

Variables opcionales:
    CHATBOT_BASE_URL
    CHATBOT_ADMIN_USER / CHATBOT_ADMIN_PASSWORD
    CHATBOT_INVENTORY_USER / CHATBOT_INVENTORY_PASSWORD
    CHATBOT_SELLER_USER / CHATBOT_SELLER_PASSWORD
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import ast
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable
from unittest.mock import patch

import requests
from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.extensions import db
from app.models import (
    Category,
    DeliveryNote,
    DeliveryNoteItem,
    Product,
    StockMovement,
    User,
)
from app.services import chatbot_service


BASE_URL = os.getenv("CHATBOT_BASE_URL", "http://localhost:5000").rstrip("/")
TIMEOUT = float(os.getenv("CHATBOT_TIMEOUT", "10"))

ROLE_CREDENTIALS = {
    "admin": (
        os.getenv("CHATBOT_ADMIN_USER", "admin"),
        os.getenv("CHATBOT_ADMIN_PASSWORD", "admin123"),
    ),
    "inventario": (
        os.getenv("CHATBOT_INVENTORY_USER", "inventario1"),
        os.getenv("CHATBOT_INVENTORY_PASSWORD", "inventario123"),
    ),
    "vendedor": (
        os.getenv("CHATBOT_SELLER_USER", "vendedor1"),
        os.getenv("CHATBOT_SELLER_PASSWORD", "vendedor123"),
    ),
}

PATHS = {
    "test": ROOT / "scripts" / "test_chatbot.py",
    "app_init": ROOT / "app" / "__init__.py",
    "pages": ROOT / "app" / "routes" / "pages.py",
    "route": ROOT / "app" / "routes" / "chatbot.py",
    "controller": ROOT / "app" / "controllers" / "chatbot_controller.py",
    "service": ROOT / "app" / "services" / "chatbot_service.py",
    "template": ROOT / "app" / "templates" / "chatbot.html",
    "base_template": ROOT / "app" / "templates" / "base_app.html",
    "js": ROOT / "app" / "static" / "js" / "chatbot.js",
    "css": ROOT / "app" / "static" / "css" / "chatbot.css",
}

BASE_CASE_IDS = {f"B{number:02d}" for number in range(1, 35)}
MANDATORY_EXTRA_IDS = {
    "C01",
    "C02",
    "C03",
    "P01",
    "P02",
    "P03",
    "P04",
    "P05",
    "P06",
    "P07",
    "P08",
    "P09",
    "P10",
    "P11",
    "P12",
    "P13",
    "P14",
    "P15",
    "P16",
    "V01",
    "V02",
    "V03",
    "V04",
    "V05",
    "V06",
    "V07",
    "V08",
    "X01",
    "X02",
    "N01",
    "N02",
    "N03",
    "N04",
    "S08",
    "S09",
    "S10",
    "S11",
    "S12",
    "S13",
    "S14",
}

CONVERSATION_KEYS = {"intent", "status", "message", "data", "suggestions"}
FORBIDDEN_RESPONSE_KEYS = {"purchase_price", "password_hash"}
SAFE_IMAGE_PATH = "/media/products/chatbot_safe_image.png"
EXTERNAL_URL_RE = re.compile(r"(?i)\b(?:https?|ftp|file)://")
PHYSICAL_PATH_RE = re.compile(
    r"(?i)(?:[a-z]:[\\/]|/(?:home|users|var|tmp|etc|opt)/)"
)
TRACE_RE = re.compile(
    r"(?i)(traceback|sqlalchemy|pymysql|werkzeug\.debug|"
    r"\bfile\s+\"[^\"]+\",\s+line\s+\d+|runtimeerror)"
)


@dataclass(frozen=True)
class Observation:
    method: str
    path: str
    status_code: int
    content_type: str
    payload: Any
    text: str
    location: str = ""
    error: str = ""
    source: str = "http-real"

    @property
    def is_json(self) -> bool:
        mimetype = self.content_type.split(";", 1)[0].strip().lower()
        return mimetype == "application/json" and self.payload is not None


class HttpLedger:
    """Cliente HTTP que registra método y ruta, nunca cuerpos ni credenciales."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.entries: list[tuple[str, str, str]] = []
        self.anonymous = self.new_session()

    @staticmethod
    def new_session() -> requests.Session:
        session = requests.Session()
        session.trust_env = False
        session.headers.update({"User-Agent": "chatbot-non-mutating-suite/1.0"})
        return session

    def request(
        self,
        session: requests.Session,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> Observation:
        method = method.upper()
        normalized_path = path if path.startswith("/") else f"/{path}"
        self.entries.append((method, normalized_path, "http-real"))
        kwargs.setdefault("timeout", TIMEOUT)
        try:
            response = session.request(
                method,
                f"{self.base_url}{normalized_path}",
                **kwargs,
            )
        except requests.RequestException as error:
            return Observation(
                method=method,
                path=normalized_path,
                status_code=0,
                content_type="",
                payload=None,
                text="",
                error=f"{type(error).__name__}: {error}",
            )

        content_type = response.headers.get("Content-Type", "")
        payload: Any = None
        try:
            payload = response.json()
        except (ValueError, requests.JSONDecodeError):
            payload = None
        return Observation(
            method=method,
            path=normalized_path,
            status_code=response.status_code,
            content_type=content_type,
            payload=payload,
            text=response.text,
            location=response.headers.get("Location", ""),
        )

    def record_test_client(self, method: str, path: str) -> None:
        self.entries.append((method.upper(), path, "flask-test-client"))

    def operational_writes(self) -> list[tuple[str, str, str]]:
        allowed_non_get = {
            ("POST", "/api/auth/login"),
            ("POST", "/api/chatbot/message"),
        }
        writes: list[tuple[str, str, str]] = []
        for method, path, source in self.entries:
            clean_path = path.split("?", 1)[0]
            if method in {"GET", "HEAD", "OPTIONS"}:
                continue
            if (method, clean_path) not in allowed_non_get:
                writes.append((method, clean_path, source))
        return writes


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    title: str
    outcome: str
    expected: str = ""
    actual: str = ""
    probable_file: str = ""
    priority: str = ""


class Runner:
    def __init__(self) -> None:
        self.results: list[CaseResult] = []
        self._ids: set[str] = set()

    def group(self, title: str) -> None:
        print(f"\n=== {title} ===")

    def check(
        self,
        case_id: str,
        title: str,
        condition: bool,
        *,
        expected: str = "",
        actual: str = "",
        probable_file: str = "",
        priority: str = "MEDIA",
    ) -> bool:
        if case_id in self._ids:
            raise RuntimeError(f"Identificador de prueba duplicado: {case_id}")
        self._ids.add(case_id)
        outcome = "OK" if bool(condition) else "FALLO"
        result = CaseResult(
            case_id=case_id,
            title=title,
            outcome=outcome,
            expected=expected,
            actual=_safe_line(actual),
            probable_file=probable_file,
            priority=priority,
        )
        self.results.append(result)
        print(f"[{outcome}] {case_id}. {title}")
        if outcome == "FALLO":
            if expected:
                print(f"       Esperado: {expected}")
            if actual:
                print(f"       Real: {_safe_line(actual)}")
        return outcome == "OK"

    def skip(self, case_id: str, title: str, reason: str) -> None:
        if case_id in self._ids:
            raise RuntimeError(f"Identificador de prueba duplicado: {case_id}")
        self._ids.add(case_id)
        self.results.append(
            CaseResult(
                case_id=case_id,
                title=title,
                outcome="SKIP",
                actual=_safe_line(reason),
            )
        )
        print(f"[SKIP] {case_id}. {title} — {_safe_line(reason)}")

    @property
    def ids(self) -> set[str]:
        return set(self._ids)

    def report(self) -> int:
        passed = sum(result.outcome == "OK" for result in self.results)
        failed = sum(result.outcome == "FALLO" for result in self.results)
        skipped = sum(result.outcome == "SKIP" for result in self.results)
        total = len(self.results)

        print("\n=== RESULTADO FINAL ===")
        print(f"TOTAL={total} OK={passed} FALLO={failed} SKIP={skipped}")
        if failed:
            print("\nFallos con causa probable:")
            for result in self.results:
                if result.outcome != "FALLO":
                    continue
                probable = result.probable_file or "por determinar"
                expected = result.expected or "cumplir el contrato probado"
                actual = result.actual or "la condición no se cumplió"
                print(
                    f"- [{result.priority}] {result.case_id} {result.title}: "
                    f"esperado={expected}; real={actual}; archivo probable={probable}"
                )
        exit_code = 1 if failed else 0
        print(f"EXIT_CODE={exit_code}")
        return exit_code


@dataclass(frozen=True)
class TableState:
    count: int
    ids: tuple[Any, ...]
    values_digest: str


@dataclass(frozen=True)
class DatabaseSnapshot:
    tables: dict[str, TableState]
    stock_total: int
    stock_by_product: tuple[tuple[int, int], ...]


def _safe_line(value: Any, limit: int = 500) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = re.sub(r"(?i)(password|secret|token|hash)\s*[:=]\s*\S+", r"\1=<redactado>", text)
    return text if len(text) <= limit else text[:limit] + "…"


def _canonical(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest()}
    return str(value)


def take_snapshot(app: Any) -> DatabaseSnapshot:
    """Lee todas las columnas; conserva solo IDs, conteos y huellas canónicas."""
    models = (
        ("products", Product),
        ("categories", Category),
        ("stock_movements", StockMovement),
        ("delivery_notes", DeliveryNote),
        ("delivery_note_items", DeliveryNoteItem),
        ("users", User),
    )
    tables: dict[str, TableState] = {}
    stock_by_product: list[tuple[int, int]] = []

    with app.app_context():
        with db.engine.connect() as connection:
            for table_name, model in models:
                table = model.__table__
                columns = list(table.columns)
                primary_keys = list(table.primary_key.columns)
                statement = select(*columns)
                if primary_keys:
                    statement = statement.order_by(*primary_keys)
                rows = connection.execute(statement).mappings().all()

                canonical_rows = [
                    {column.name: _canonical(row[column.name]) for column in columns}
                    for row in rows
                ]
                digest_input = json.dumps(
                    canonical_rows,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                ids = tuple(
                    _canonical(row[primary_keys[0].name])
                    for row in rows
                ) if primary_keys else tuple()
                tables[table_name] = TableState(
                    count=len(rows),
                    ids=ids,
                    values_digest=hashlib.sha256(digest_input).hexdigest(),
                )

                if table_name == "products":
                    stock_by_product = sorted(
                        (int(row["id"]), int(row["current_stock"]))
                        for row in rows
                    )

    return DatabaseSnapshot(
        tables=tables,
        stock_total=sum(stock for _, stock in stock_by_product),
        stock_by_product=tuple(stock_by_product),
    )


def table_unchanged(
    before: DatabaseSnapshot,
    after: DatabaseSnapshot,
    table_name: str,
) -> bool:
    return before.tables.get(table_name) == after.tables.get(table_name)


def all_tables_unchanged(
    before: DatabaseSnapshot,
    after: DatabaseSnapshot,
) -> bool:
    return before.tables == after.tables


def snapshot_actual(
    before: DatabaseSnapshot,
    after: DatabaseSnapshot | None,
    table_names: Iterable[str],
) -> str:
    if after is None:
        return "snapshot final no disponible"
    parts = []
    for name in table_names:
        left = before.tables[name]
        right = after.tables[name]
        parts.append(
            f"{name}: count {left.count}->{right.count}, "
            f"ids={'igual' if left.ids == right.ids else 'cambió'}, "
            f"valores={'igual' if left.values_digest == right.values_digest else 'cambió'}"
        )
    return "; ".join(parts)


def read_sources() -> dict[str, str]:
    missing = [str(path) for path in PATHS.values() if not path.is_file()]
    if missing:
        raise RuntimeError(f"Faltan archivos requeridos: {', '.join(missing)}")
    return {
        name: path.read_text(encoding="utf-8")
        for name, path in PATHS.items()
    }


def observation_summary(observation: Observation | None) -> str:
    if observation is None:
        return "sin observación"
    if observation.error:
        return f"HTTP=0 error={observation.error}"
    keys: list[str] = []
    intent = ""
    semantic_status = ""
    if isinstance(observation.payload, dict):
        keys = sorted(str(key) for key in observation.payload)
        intent = str(observation.payload.get("intent", ""))
        semantic_status = str(observation.payload.get("status", ""))
    return (
        f"HTTP={observation.status_code}, Content-Type={observation.content_type!r}, "
        f"keys={keys}, intent={intent!r}, status={semantic_status!r}"
    )


def is_json_status(observation: Observation | None, status_code: int) -> bool:
    return bool(
        observation is not None
        and observation.status_code == status_code
        and observation.is_json
        and isinstance(observation.payload, dict)
    )


def has_conversation_contract(payload: Any) -> bool:
    return bool(
        isinstance(payload, dict)
        and CONVERSATION_KEYS.issubset(payload)
        and isinstance(payload.get("intent"), str)
        and isinstance(payload.get("status"), str)
        and isinstance(payload.get("message"), str)
        and isinstance(payload.get("data"), dict)
        and isinstance(payload.get("suggestions"), list)
    )


def conversation_matches(
    observation: Observation | None,
    intent: str,
    statuses: set[str],
) -> bool:
    return bool(
        is_json_status(observation, 200)
        and has_conversation_contract(observation.payload)
        and observation.payload.get("intent") == intent
        and observation.payload.get("status") in statuses
    )


def response_product(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    product = data.get("product")
    return product if isinstance(product, dict) else None


def response_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return []
    items = payload["data"].get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def response_categories(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return []
    categories = payload["data"].get("categories")
    if not isinstance(categories, list):
        return []
    return [category for category in categories if isinstance(category, dict)]


def category_clarification_contract(payload: Any) -> tuple[bool, str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    categories = response_categories(payload)
    suggestions = payload.get("suggestions") if isinstance(payload, dict) else None
    expected_suggestions = [
        f"Productos de la categoría {category.get('name')}"
        for category in categories
    ]
    category_shape_ok = all(
        set(category) == {"id", "name", "description"}
        and isinstance(category.get("id"), int)
        and isinstance(category.get("name"), str)
        and bool(category.get("name").strip())
        and (
            category.get("description") is None
            or isinstance(category.get("description"), str)
        )
        for category in categories
    )
    condition = bool(
        isinstance(data, dict)
        and 2 <= len(categories) <= chatbot_service.MAX_AMBIGUOUS_CANDIDATES
        and data.get("count") == len(categories)
        and "items" not in data
        and "product" not in data
        and category_shape_ok
        and suggestions == expected_suggestions
    )
    detail = (
        f"count={data.get('count') if isinstance(data, dict) else None}, "
        f"categories={len(categories)}, "
        f"data_keys={sorted(data) if isinstance(data, dict) else []}, "
        f"suggestions_match={suggestions == expected_suggestions}"
    )
    return condition, detail


def product_clarification_contract(payload: Any) -> tuple[bool, str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    items = response_items(payload)
    suggestions = payload.get("suggestions") if isinstance(payload, dict) else None
    expected_suggestions = [f"Código {item.get('code')}" for item in items]
    condition = bool(
        isinstance(data, dict)
        and 2 <= len(items) <= chatbot_service.MAX_AMBIGUOUS_CANDIDATES
        and data.get("count") == len(items)
        and "categories" not in data
        and "product" not in data
        and all(
            {"id", "code", "name", "category", "image_url"}.issubset(item)
            for item in items
        )
        and suggestions == expected_suggestions
    )
    detail = (
        f"count={data.get('count') if isinstance(data, dict) else None}, "
        f"items={len(items)}, "
        f"data_keys={sorted(data) if isinstance(data, dict) else []}, "
        f"suggestions_match={suggestions == expected_suggestions}"
    )
    return condition, detail


def price_response_contract(
    observation: Observation | None,
    expected_sale_price: Any,
) -> tuple[bool, str]:
    product = response_product(observation.payload) if observation else None
    numeric_price = product.get("sale_price") if product else None
    message = (
        str(observation.payload.get("message", ""))
        if observation and isinstance(observation.payload, dict)
        else ""
    )
    try:
        expected_text = f"{float(expected_sale_price):.2f}".replace(".", ",")
    except (TypeError, ValueError):
        expected_text = ""
    condition = bool(
        product is not None
        and isinstance(numeric_price, (int, float))
        and not isinstance(numeric_price, bool)
        and numeric_price == expected_sale_price
        and expected_text
        and expected_text in message
        and re.search(r"(?<!\d)\d+,\d{2}(?!\d)", message)
    )
    return condition, (
        f"sale_price_type={type(numeric_price).__name__}, "
        f"textual_comma_2_decimals={bool(expected_text and expected_text in message)}"
    )


def contains_key(value: Any, keys: set[str]) -> bool:
    lowered = {key.lower() for key in keys}
    if isinstance(value, dict):
        if any(str(key).lower() in lowered for key in value):
            return True
        return any(contains_key(item, lowered) for item in value.values())
    if isinstance(value, list):
        return any(contains_key(item, lowered) for item in value)
    return False


def security_violations(value: Any, path: str = "$") -> list[str]:
    violations: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            item_path = f"{path}.{key}"
            if str(key).lower() in FORBIDDEN_RESPONSE_KEYS:
                violations.append(f"campo_prohibido@{item_path}")
            violations.extend(security_violations(item, item_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            violations.extend(security_violations(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        lowered = value.lower().replace("\\", "/")
        if "uploads/" in lowered:
            violations.append(f"ruta_uploads@{path}")
        if EXTERNAL_URL_RE.search(value):
            violations.append(f"url_externa@{path}")
        if PHYSICAL_PATH_RE.search(value):
            violations.append(f"ruta_fisica@{path}")
    return violations


def safe_fixture(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    return {
        key: item.get(key)
        for key in (
            "id",
            "code",
            "name",
            "category_id",
            "category",
            "current_stock",
            "minimum_stock",
            "sale_price",
            "image_url",
            "is_active",
        )
    }


def list_payload_items(observation: Observation) -> list[dict[str, Any]]:
    if not isinstance(observation.payload, dict):
        return []
    items = observation.payload.get("items")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def find_ambiguity_term(app: Any, active_products: list[dict[str, Any]]) -> str | None:
    ignored = {"de", "del", "la", "el", "los", "las", "para", "por", "con"}
    terms: set[str] = set()
    for product in active_products:
        name = chatbot_service.normalize_text(str(product.get("name") or ""))
        terms.update(
            word
            for word in re.findall(r"[a-z0-9ñ]+", name)
            if len(word) >= 3 and word not in ignored and not word.isdigit()
        )
    with app.app_context():
        for term in sorted(terms):
            lookup = chatbot_service.find_product(term)
            if len(lookup.candidates) >= 2:
                return term
    return None


def find_category_ambiguity_query(
    app: Any,
    categories: list[dict[str, Any]],
) -> str | None:
    exact_names = {
        chatbot_service.normalize_text(str(category.get("name") or ""))
        for category in categories
    }
    candidates: set[str] = set()
    for category in categories:
        normalized = chatbot_service.normalize_text(
            str(category.get("name") or "")
        )
        words = normalized.split()
        candidates.update(word for word in words if len(word) >= 3)
        for end in range(2, len(words)):
            candidates.add(" ".join(words[:end]))
        for start in range(1, max(1, len(words) - 1)):
            candidates.add(" ".join(words[start:]))
    candidates.difference_update(exact_names)

    with app.app_context():
        for candidate in sorted(candidates, key=lambda value: (len(value), value)):
            _, ambiguous = chatbot_service._find_category(candidate)  # noqa: SLF001
            if len(ambiguous) >= 2:
                return candidate
    return None


def fake_ambiguity_contract() -> tuple[bool, str, dict[str, Any]]:
    category = SimpleNamespace(name="Herramientas simuladas")
    fake_products = [
        SimpleNamespace(
            id=91001,
            code="FAKE-A",
            name="Martillo carpintero",
            category=category,
            image_url=None,
        ),
        SimpleNamespace(
            id=91002,
            code="FAKE-B",
            name="Martillo de goma",
            category=category,
            image_url=None,
        ),
    ]
    with patch.object(chatbot_service, "_active_products", return_value=fake_products):
        lookup = chatbot_service.find_product("martillo")
    response = chatbot_service._ambiguous_response(  # noqa: SLF001
        chatbot_service.INTENT_PRODUCT_BY_NAME,
        lookup.candidates,
    )
    condition = (
        len(lookup.candidates) == 2
        and response.get("status") == chatbot_service.STATUS_NEEDS_CLARIFICATION
        and response.get("data", {}).get("count") == 2
    )
    return (
        condition,
        f"candidates={len(lookup.candidates)}, status={response.get('status')!r}",
        response,
    )


def fake_category_ambiguity_response() -> dict[str, Any]:
    categories = [
        SimpleNamespace(
            id=92001,
            name="Electricidad simulada",
            description="Primera categoría simulada",
        ),
        SimpleNamespace(
            id=92002,
            name="Electricidad y accesorios simulada",
            description=None,
        ),
    ]
    with patch.object(
        chatbot_service,
        "_find_category",
        return_value=(None, categories),
    ):
        return chatbot_service._category_products_response(  # noqa: SLF001
            "electricidad simulada",
            "admin",
        )


def css_property_rem(source: str, selector: str, property_name: str) -> float | None:
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", source):
        selectors = [item.strip() for item in match.group(1).split(",")]
        if selector not in selectors:
            continue
        property_match = re.search(
            rf"{re.escape(property_name)}\s*:\s*([0-9]*\.?[0-9]+)rem\b",
            match.group(2),
        )
        if property_match:
            return float(property_match.group(1))
    return None


def make_missing_code(codes: set[str]) -> str:
    base = "CHATBOT-NO-EXISTE-9F3A"
    candidate = base
    suffix = 1
    while candidate.lower() in codes:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def make_code_typo(code: str, codes: set[str]) -> str:
    if len(code) < 50:
        candidate = f"{code}X"
    else:
        replacement = "X" if code[-1].upper() != "X" else "Y"
        candidate = f"{code[:-1]}{replacement}"
    suffix = 1
    while candidate.lower() in codes:
        tail = str(suffix)
        candidate = f"{code[: max(1, 50 - len(tail))]}{tail}"
        suffix += 1
    return candidate


def find_fuzzy_case(
    app: Any,
    active_products: list[dict[str, Any]],
) -> tuple[dict[str, Any], str, float] | None:
    replacements = ("x", "z", "q")
    with app.app_context():
        for product in active_products:
            name = str(product.get("name") or "")
            normalized_name = chatbot_service.normalize_text(name)
            if len(normalized_name) < chatbot_service.FUZZY_MIN_LENGTH:
                continue
            for index, character in enumerate(name):
                if not character.isalpha():
                    continue
                for replacement in replacements:
                    if character.lower() == replacement:
                        continue
                    variant = f"{name[:index]}{replacement}{name[index + 1:]}"
                    normalized_variant = chatbot_service.normalize_text(variant)
                    if (
                        len(normalized_variant) < chatbot_service.FUZZY_MIN_LENGTH
                        or normalized_variant in normalized_name
                        or normalized_name in normalized_variant
                    ):
                        continue
                    ratio = SequenceMatcher(
                        None,
                        normalized_variant,
                        normalized_name,
                        autojunk=False,
                    ).ratio()
                    if ratio < chatbot_service.FUZZY_THRESHOLD:
                        continue
                    lookup = chatbot_service.find_product(variant)
                    if (
                        lookup.product is not None
                        and lookup.product.id == product.get("id")
                    ):
                        return product, variant, ratio
    return None


def persistence_calls_absent(source: str) -> tuple[bool, list[str]]:
    tree = ast.parse(source)
    session_mutations = {
        "add",
        "add_all",
        "bulk_save_objects",
        "commit",
        "delete",
        "flush",
        "merge",
        "rollback_to_savepoint",
    }
    query_mutations = {"delete", "update"}
    forbidden_names = {"insert", "update", "delete"}
    found: list[str] = []

    def attribute_chain(node: ast.AST) -> list[str]:
        parts: list[str] = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return list(reversed(parts))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Attribute):
            chain = attribute_chain(function)
            owners = set(chain[:-1])
            if (
                function.attr in session_mutations
                and "session" in owners
            ) or (
                function.attr in query_mutations
                and "query" in owners
            ):
                found.append(".".join(chain))
        elif isinstance(function, ast.Name) and function.id in forbidden_names:
            found.append(function.id)
    return not found, sorted(set(found))


def generic_500_observation(app: Any, user_id: int) -> Observation:
    marker = "SENSITIVE_INTERNAL_MARKER"
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["_user_id"] = str(user_id)
        flask_session["_fresh"] = True

    with (
        patch.object(
            chatbot_service,
            "process_message",
            side_effect=RuntimeError(marker),
        ),
        patch.object(app.logger, "exception"),
    ):
        response = client.post(
            "/api/chatbot/message",
            json={"message": "Hola"},
        )

    payload = response.get_json(silent=True)
    return Observation(
        method="POST",
        path="/api/chatbot/message",
        status_code=response.status_code,
        content_type=response.headers.get("Content-Type", ""),
        payload=payload,
        text=response.get_data(as_text=True),
        source="flask-test-client",
    )


def login(
    http: HttpLedger,
    role: str,
) -> tuple[requests.Session, Observation]:
    identifier, password = ROLE_CREDENTIALS[role]
    session = http.new_session()
    observation = http.request(
        session,
        "POST",
        "/api/auth/login",
        json={"identifier": identifier, "password": password},
        headers={"Accept": "application/json"},
    )
    return session, observation


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("SUITE NO MUTANTE DEL CHATBOT")
    print(f"Base URL: {BASE_URL}")

    runner = Runner()
    http = HttpLedger(BASE_URL)

    try:
        sources = read_sources()
        app = create_app()
        baseline = take_snapshot(app)
    except Exception as error:  # noqa: BLE001 - error de setup, sin datos sensibles
        print(f"[SETUP-ERROR] No fue posible leer contrato/BD: {type(error).__name__}")
        print("EXIT_CODE=2")
        return 2

    login_page_setup = http.request(
        http.anonymous,
        "GET",
        "/login",
        allow_redirects=False,
    )
    db_health = http.request(
        http.anonymous,
        "GET",
        "/health/db",
        allow_redirects=False,
    )
    if login_page_setup.status_code != 200 or not is_json_status(db_health, 200):
        print(
            "[SETUP-ERROR] Flask o MySQL no están disponibles: "
            f"/login={login_page_setup.status_code}, /health/db={db_health.status_code}"
        )
        print("Inicie el entorno con: python run.py")
        print("EXIT_CODE=2")
        return 2

    sessions: dict[str, requests.Session] = {}
    login_observations: dict[str, Observation] = {}
    for role in ("admin", "inventario", "vendedor"):
        session, observation = login(http, role)
        sessions[role] = session
        login_observations[role] = observation
    bad_logins = [
        role
        for role, observation in login_observations.items()
        if not is_json_status(observation, 200)
    ]
    if bad_logins:
        detail = "; ".join(
            f"{role}={login_observations[role].status_code}"
            for role in bad_logins
        )
        print(f"[SETUP-ERROR] No se pudieron abrir las sesiones requeridas: {detail}")
        print("Ajuste las variables CHATBOT_* sin editar .env.")
        print("EXIT_CODE=2")
        return 2

    admin = sessions["admin"]
    inventario = sessions["inventario"]
    vendedor = sessions["vendedor"]

    products_initial = http.request(admin, "GET", "/api/products")
    products_all = http.request(
        admin,
        "GET",
        "/api/products?include_inactive=true",
    )
    categories_initial = http.request(admin, "GET", "/api/categories")
    me_initial = http.request(admin, "GET", "/api/auth/me")

    active_products = [
        fixture
        for item in list_payload_items(products_initial)
        if (fixture := safe_fixture(item)) is not None
        and fixture.get("is_active") is True
    ]
    all_product_fixtures = [
        fixture
        for item in list_payload_items(products_all)
        if (fixture := safe_fixture(item)) is not None
    ]
    inactive_products = [
        product
        for product in all_product_fixtures
        if product.get("is_active") is False
    ]
    categories = [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "description": item.get("description"),
        }
        for item in list_payload_items(categories_initial)
    ]
    selected = (
        sorted(
            active_products,
            key=lambda product: (
                not bool(product.get("image_url")),
                len(str(product.get("name") or "")),
                int(product.get("id") or 0),
            ),
        )[0]
        if active_products
        else None
    )
    codes = {
        str(product.get("code") or "").lower()
        for product in all_product_fixtures
    }
    missing_code = make_missing_code(codes)

    print(
        "[SETUP-OK] "
        f"products={baseline.tables['products'].count}, "
        f"active={len(active_products)}, inactive={len(inactive_products)}, "
        f"categories={baseline.tables['categories'].count}, "
        f"movements={baseline.tables['stock_movements'].count}, "
        f"delivery_notes={baseline.tables['delivery_notes'].count}, "
        f"delivery_note_items={baseline.tables['delivery_note_items'].count}, "
        f"users={baseline.tables['users'].count}, "
        f"stock_total={baseline.stock_total}"
    )

    chatbot_observations: list[tuple[str, Observation]] = []

    def send(
        role_label: str,
        session: requests.Session,
        *,
        message: str | None = None,
        json_body: Any = None,
        raw_body: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Observation:
        kwargs: dict[str, Any] = {
            "headers": headers or {"Accept": "application/json"},
        }
        if raw_body is not None:
            kwargs["data"] = raw_body
        elif json_body is not None:
            kwargs["json"] = json_body
        else:
            kwargs["json"] = {"message": message}
        observation = http.request(
            session,
            "POST",
            "/api/chatbot/message",
            **kwargs,
        )
        chatbot_observations.append((role_label, observation))
        return observation

    runner.group("Base aprobada B01-B21")

    page_unauth = http.request(
        http.anonymous,
        "GET",
        "/chatbot",
        allow_redirects=False,
    )
    runner.check(
        "B01",
        "/chatbot sin sesión redirige a /login",
        page_unauth.status_code == 302 and "/login" in page_unauth.location,
        expected="HTTP 302 con Location /login",
        actual=observation_summary(page_unauth) + f", Location={page_unauth.location!r}",
        probable_file="app/routes/pages.py",
        priority="ALTA",
    )

    chatbot_pages = {
        role: http.request(session, "GET", "/chatbot")
        for role, session in sessions.items()
    }
    runner.check(
        "B02",
        "Admin accede a /chatbot",
        chatbot_pages["admin"].status_code == 200
        and chatbot_pages["admin"].content_type.startswith("text/html")
        and "Asistente de Administración" in chatbot_pages["admin"].text
        and 'data-can-view-stock="true"' in chatbot_pages["admin"].text,
        expected="HTTP 200 HTML con controles de stock",
        actual=observation_summary(chatbot_pages["admin"]),
        probable_file="app/routes/pages.py / app/templates/chatbot.html",
    )
    runner.check(
        "B03",
        "Inventario accede a /chatbot",
        chatbot_pages["inventario"].status_code == 200
        and chatbot_pages["inventario"].content_type.startswith("text/html")
        and 'data-can-view-stock="true"' in chatbot_pages["inventario"].text
        and "Asistente de Inventario" in chatbot_pages["inventario"].text,
        expected="HTTP 200 HTML con controles de stock",
        actual=observation_summary(chatbot_pages["inventario"]),
        probable_file="app/routes/pages.py / app/templates/chatbot.html",
    )
    runner.check(
        "B04",
        "Vendedor accede a /chatbot",
        chatbot_pages["vendedor"].status_code == 200
        and chatbot_pages["vendedor"].content_type.startswith("text/html")
        and 'data-can-view-stock="false"' in chatbot_pages["vendedor"].text,
        expected="HTTP 200 HTML sin permiso de stock exacto",
        actual=observation_summary(chatbot_pages["vendedor"]),
        probable_file="app/routes/pages.py / app/templates/chatbot.html",
    )

    unauth_post = send("sin_sesion", http.anonymous, message="Hola")
    runner.check(
        "B05",
        "POST sin sesión devuelve 401 JSON",
        is_json_status(unauth_post, 401)
        and isinstance(unauth_post.payload.get("error"), str),
        expected="HTTP 401 application/json con error",
        actual=observation_summary(unauth_post),
        probable_file="app/utils/auth_decorators.py",
        priority="ALTA",
    )

    empty_message = send("admin", admin, message="   ")
    runner.check(
        "B06",
        "Mensaje vacío devuelve 400",
        is_json_status(empty_message, 400),
        expected="HTTP 400 application/json",
        actual=observation_summary(empty_message),
        probable_file="app/controllers/chatbot_controller.py",
    )

    too_long = send("admin", admin, message="x" * 501)
    runner.check(
        "B07",
        "Mensaje mayor de 500 devuelve 400",
        is_json_status(too_long, 400),
        expected="HTTP 400 application/json",
        actual=observation_summary(too_long),
        probable_file="app/controllers/chatbot_controller.py",
    )

    greeting = send("admin", admin, message="Hola")
    runner.check(
        "B08",
        "Saludo reconoce greeting",
        conversation_matches(greeting, "greeting", {"ok"}),
        expected="HTTP 200 intent=greeting status=ok",
        actual=observation_summary(greeting),
        probable_file="app/services/chatbot_service.py",
    )

    help_response = send("admin", admin, message="Ayuda")
    runner.check(
        "B09",
        "Ayuda reconoce help",
        conversation_matches(help_response, "help", {"ok"})
        and bool(help_response.payload.get("suggestions")),
        expected="HTTP 200 intent=help status=ok con sugerencias",
        actual=observation_summary(help_response),
        probable_file="app/services/chatbot_service.py",
    )

    product_by_name: Observation | None = None
    product_by_code: Observation | None = None
    stock_response: Observation | None = None
    price_response: Observation | None = None
    category_response: Observation | None = None
    category_products_response: Observation | None = None

    if selected is None:
        for case_id, title in (
            ("B10", "Consulta por nombre"),
            ("B11", "Consulta por código"),
            ("B12", "Consulta de stock"),
            ("B13", "Consulta de precio"),
            ("B14", "Consulta de categoría"),
            ("B15", "Productos por categoría"),
        ):
            runner.skip(case_id, title, "No hay productos activos existentes.")
    else:
        selected_id = selected.get("id")
        selected_name = str(selected.get("name") or "")
        selected_code = str(selected.get("code") or "")
        selected_category = str(selected.get("category") or "")

        product_by_name = send(
            "admin",
            admin,
            message=f"Buscar producto {selected_name}",
        )
        named_product = response_product(product_by_name.payload)
        runner.check(
            "B10",
            "Consulta por nombre usa producto activo existente",
            conversation_matches(product_by_name, "product_by_name", {"ok"})
            and named_product is not None
            and named_product.get("id") == selected_id,
            expected=f"HTTP 200 product_by_name para id activo {selected_id}",
            actual=observation_summary(product_by_name),
            probable_file="app/services/chatbot_service.py",
        )

        product_by_code = send(
            "admin",
            admin,
            message=f"Código {selected_code}",
        )
        coded_product = response_product(product_by_code.payload)
        runner.check(
            "B11",
            "Consulta por código exige coincidencia exacta",
            conversation_matches(product_by_code, "product_by_code", {"ok"})
            and coded_product is not None
            and coded_product.get("id") == selected_id,
            expected=f"HTTP 200 product_by_code para id activo {selected_id}",
            actual=observation_summary(product_by_code),
            probable_file="app/services/chatbot_service.py",
        )

        stock_response = send(
            "admin",
            admin,
            message=f"Stock exacto de {selected_name}",
        )
        stock_product = response_product(stock_response.payload)
        runner.check(
            "B12",
            "Consulta de stock devuelve cantidades autorizadas",
            conversation_matches(stock_response, "product_stock", {"ok"})
            and stock_product is not None
            and stock_product.get("current_stock") == selected.get("current_stock")
            and stock_product.get("minimum_stock") == selected.get("minimum_stock"),
            expected="HTTP 200 con current_stock y minimum_stock reales",
            actual=observation_summary(stock_response),
            probable_file="app/services/chatbot_service.py",
        )

        price_response = send(
            "admin",
            admin,
            message=f"Precio de {selected_name}",
        )
        priced_product = response_product(price_response.payload)
        price_contract_ok, price_contract_actual = price_response_contract(
            price_response,
            selected.get("sale_price"),
        )
        runner.check(
            "B13",
            "Consulta de precio devuelve texto y sale_price aprobados",
            conversation_matches(price_response, "product_price", {"ok"})
            and priced_product is not None
            and priced_product.get("sale_price") == selected.get("sale_price")
            and price_contract_ok,
            expected=(
                "HTTP 200, sale_price numérico y texto con coma/dos decimales"
            ),
            actual=(
                observation_summary(price_response)
                + f", {price_contract_actual}"
            ),
            probable_file="app/services/chatbot_service.py",
        )

        category_response = send(
            "admin",
            admin,
            message=f"Categoría de {selected_name}",
        )
        categorized_product = response_product(category_response.payload)
        runner.check(
            "B14",
            "Consulta de categoría devuelve categoría real",
            conversation_matches(category_response, "product_category", {"ok"})
            and categorized_product is not None
            and categorized_product.get("category") == selected.get("category"),
            expected="HTTP 200 con categoría del producto activo",
            actual=observation_summary(category_response),
            probable_file="app/services/chatbot_service.py",
        )

        category_products_response = send(
            "admin",
            admin,
            message=f"Productos de la categoría {selected_category}",
        )
        category_items = response_items(category_products_response.payload)
        runner.check(
            "B15",
            "Productos por categoría devuelve activos de la categoría",
            conversation_matches(
                category_products_response,
                "products_by_category",
                {"ok"},
            )
            and any(item.get("id") == selected_id for item in category_items)
            and all(
                item.get("category") == selected_category
                for item in category_items
            ),
            expected="HTTP 200 status=ok e incluye el producto activo seleccionado",
            actual=observation_summary(category_products_response),
            probable_file="app/services/chatbot_service.py",
        )

    low_stock = send("admin", admin, message="Productos bajo stock")
    runner.check(
        "B16",
        "Listado de bajo stock",
        conversation_matches(low_stock, "low_stock_products", {"ok", "empty"}),
        expected="HTTP 200 status ok o empty",
        actual=observation_summary(low_stock),
        probable_file="app/services/chatbot_service.py",
    )

    out_of_stock = send("admin", admin, message="Productos agotados")
    runner.check(
        "B17",
        "Listado de agotados",
        conversation_matches(out_of_stock, "out_of_stock_products", {"ok", "empty"}),
        expected="HTTP 200 status ok o empty",
        actual=observation_summary(out_of_stock),
        probable_file="app/services/chatbot_service.py",
    )

    missing = send("admin", admin, message=f"Código {missing_code}")
    runner.check(
        "B18",
        "Producto inexistente responde not_found sin inventar",
        conversation_matches(missing, "product_by_code", {"not_found"})
        and response_product(missing.payload) is None
        and not response_items(missing.payload),
        expected="HTTP 200 status=not_found sin producto ni lista fabricada",
        actual=observation_summary(missing),
        probable_file="app/services/chatbot_service.py",
        priority="ALTA",
    )

    unknown = send("admin", admin, message="xyzzy quux")
    runner.check(
        "B19",
        "Consulta desconocida responde unknown",
        conversation_matches(unknown, "unknown", {"unknown"}),
        expected="HTTP 200 intent=unknown status=unknown",
        actual=observation_summary(unknown),
        probable_file="app/services/chatbot_service.py",
    )

    product_ambiguity_payload: dict[str, Any] | None = None
    ambiguity_term = find_ambiguity_term(app, active_products)
    if ambiguity_term:
        ambiguous = send(
            "admin",
            admin,
            message=f"Buscar producto {ambiguity_term}",
        )
        ambiguous_items = response_items(ambiguous.payload)
        product_ambiguity_payload = (
            ambiguous.payload if isinstance(ambiguous.payload, dict) else None
        )
        product_contract_ok, product_contract_actual = (
            product_clarification_contract(product_ambiguity_payload)
        )
        runner.check(
            "B20",
            "Consulta ambigua pide aclaración",
            conversation_matches(
                ambiguous,
                "product_by_name",
                {"needs_clarification"},
            )
            and len(ambiguous_items) >= 2
            and product_contract_ok,
            expected=(
                "HTTP 200 status=needs_clarification con contrato de productos"
            ),
            actual=(
                observation_summary(ambiguous)
                + f", {product_contract_actual}"
            ),
            probable_file="app/services/chatbot_service.py",
        )
    else:
        fake_ok, fake_actual, fake_response = fake_ambiguity_contract()
        product_ambiguity_payload = fake_response
        runner.check(
            "B20",
            "Consulta ambigua pide aclaración (helper puro)",
            fake_ok,
            expected="needs_clarification con dos objetos simulados",
            actual=fake_actual,
            probable_file="app/services/chatbot_service.py",
        )

    if selected is None or product_by_code is None:
        runner.skip("B21", "image_url segura en respuesta", "No hay producto activo.")
    else:
        coded_product = response_product(product_by_code.payload) or {}
        expected_image = chatbot_service.safe_product_image_url(
            selected.get("image_url")
        )
        runner.check(
            "B21",
            "image_url se normaliza por lista blanca",
            coded_product.get("image_url") == expected_image
            and not security_violations(coded_product.get("image_url")),
            expected=f"image_url={expected_image!r}",
            actual=f"image_url={coded_product.get('image_url')!r}",
            probable_file="app/services/chatbot_service.py",
            priority="ALTA",
        )

    runner.group("Contratos aprobados de ambigüedad y precio")

    category_ambiguity_query = find_category_ambiguity_query(app, categories)
    if category_ambiguity_query:
        category_ambiguous = send(
            "admin",
            admin,
            message=f"Productos de la categoría {category_ambiguity_query}",
        )
        category_contract_ok, category_contract_actual = (
            category_clarification_contract(category_ambiguous.payload)
        )
        runner.check(
            "C01",
            "Categoría ambigua usa data.categories sin product cards",
            conversation_matches(
                category_ambiguous,
                "products_by_category",
                {"needs_clarification"},
            )
            and category_contract_ok,
            expected=(
                "data.categories[id,name,description], máximo 5, sin items/product "
                "y sugerencias por nombre"
            ),
            actual=(
                observation_summary(category_ambiguous)
                + f", {category_contract_actual}"
            ),
            probable_file="app/services/chatbot_service.py",
            priority="ALTA",
        )
    else:
        category_fake = fake_category_ambiguity_response()
        category_contract_ok, category_contract_actual = (
            category_clarification_contract(category_fake)
        )
        runner.check(
            "C01",
            "Categoría ambigua usa data.categories (helper puro)",
            category_fake.get("intent") == "products_by_category"
            and category_fake.get("status") == "needs_clarification"
            and category_contract_ok,
            expected=(
                "data.categories[id,name,description], máximo 5, sin items/product "
                "y sugerencias por nombre"
            ),
            actual=category_contract_actual,
            probable_file="app/services/chatbot_service.py",
            priority="ALTA",
        )

    product_contract_ok, product_contract_actual = (
        product_clarification_contract(product_ambiguity_payload)
    )
    runner.check(
        "C02",
        "Producto ambiguo conserva data.items y sugerencias por código",
        product_contract_ok,
        expected=(
            "data.items de 2 a 5, sin data.categories/product y sugerencias Código"
        ),
        actual=product_contract_actual,
        probable_file="app/services/chatbot_service.py",
        priority="ALTA",
    )

    if selected is None or price_response is None:
        runner.skip(
            "C03",
            "Precio textual usa coma/dos decimales y sale_price numérico",
            "No hay producto activo existente.",
        )
    else:
        price_contract_ok, price_contract_actual = price_response_contract(
            price_response,
            selected.get("sale_price"),
        )
        runner.check(
            "C03",
            "Precio textual usa coma/dos decimales y sale_price numérico",
            price_contract_ok,
            expected="Mensaje N,NN y JSON sale_price numérico",
            actual=price_contract_actual,
            probable_file="app/services/chatbot_service.py",
            priority="ALTA",
        )

    runner.group("Permisos por rol")

    permission_results: list[bool] = []
    if selected is None:
        for case_id, title in (
            ("P01", "Vendedor consulta sale_price"),
            ("P02", "Vendedor consulta disponibilidad sin stock interno"),
            ("P03", "Vendedor pregunta stock exacto sin recibir cantidad"),
            ("P05", "Inventario recibe stock exacto"),
            ("P07", "Inventario consulta sale_price"),
        ):
            runner.skip(case_id, title, "No hay producto activo existente.")
    else:
        name = str(selected.get("name") or "")

        seller_price = send("vendedor", vendedor, message=f"Precio de {name}")
        seller_price_product = response_product(seller_price.payload)
        permission_results.append(
            runner.check(
                "P01",
                "Vendedor price -> 200 con sale_price",
                conversation_matches(seller_price, "product_price", {"ok"})
                and seller_price_product is not None
                and "sale_price" in seller_price_product
                and not contains_key(seller_price.payload, {"purchase_price"}),
                expected="HTTP 200 con sale_price, nunca purchase_price",
                actual=observation_summary(seller_price),
                probable_file="app/services/chatbot_service.py",
                priority="ALTA",
            )
        )

        seller_status = send(
            "vendedor",
            vendedor,
            message=f"Disponibilidad de {name}",
        )
        seller_status_product = response_product(seller_status.payload)
        permission_results.append(
            runner.check(
                "P02",
                "Vendedor availability/product_status oculta stock interno",
                conversation_matches(seller_status, "product_status", {"ok"})
                and seller_status_product is not None
                and "availability" in seller_status_product
                and not contains_key(
                    seller_status.payload,
                    {"current_stock", "minimum_stock"},
                ),
                expected="HTTP 200 con availability sin current_stock/minimum_stock",
                actual=observation_summary(seller_status),
                probable_file="app/services/chatbot_service.py",
                priority="ALTA",
            )
        )

        seller_stock = send(
            "vendedor",
            vendedor,
            message=f"Stock exacto de {name}",
        )
        seller_stock_product = response_product(seller_stock.payload)
        seller_message = (
            str(seller_stock.payload.get("message", ""))
            if isinstance(seller_stock.payload, dict)
            else ""
        )
        message_without_name = seller_message.replace(name, "")
        permission_results.append(
            runner.check(
                "P03",
                "Vendedor stock exacto responde conversacional sin cantidad",
                conversation_matches(seller_stock, "product_stock", {"ok"})
                and seller_stock_product is not None
                and "availability" in seller_stock_product
                and not contains_key(
                    seller_stock.payload,
                    {"current_stock", "minimum_stock"},
                )
                and re.search(r"(?<!\w)-?\d+(?:[.,]\d+)?", message_without_name)
                is None,
                expected="HTTP 200, availability y ningún valor/campo de stock",
                actual=observation_summary(seller_stock),
                probable_file="app/services/chatbot_service.py",
                priority="CRÍTICA",
            )
        )

        inventory_stock = send(
            "inventario",
            inventario,
            message=f"Stock exacto de {name}",
        )
        inventory_stock_product = response_product(inventory_stock.payload)
        permission_results.append(
            runner.check(
                "P05",
                "Inventario stock exacto incluye current_stock/minimum_stock",
                conversation_matches(inventory_stock, "product_stock", {"ok"})
                and inventory_stock_product is not None
                and inventory_stock_product.get("current_stock")
                == selected.get("current_stock")
                and inventory_stock_product.get("minimum_stock")
                == selected.get("minimum_stock"),
                expected="HTTP 200 con ambas cantidades reales",
                actual=observation_summary(inventory_stock),
                probable_file="app/services/chatbot_service.py",
                priority="ALTA",
            )
        )

        inventory_price = send(
            "inventario",
            inventario,
            message=f"Precio de {name}",
        )
        inventory_price_product = response_product(inventory_price.payload)
        permission_results.append(
        runner.check(
            "P07",
            "Inventario no recibe precio de venta",
            is_json_status(inventory_price, 403)
            and not contains_key(inventory_price.payload, {"sale_price", "purchase_price", "unit_cost"}),
            expected="HTTP 403 sin sale_price, purchase_price ni unit_cost",
            actual=observation_summary(inventory_price),
            probable_file="app/services/chatbot_service.py",
            priority="ALTA",
        )
        )

    seller_low = send("vendedor", vendedor, message="Productos bajo stock")
    permission_results.append(
        runner.check(
            "P04",
            "Vendedor low_stock -> 403 JSON sin lista",
            is_json_status(seller_low, 403)
            and isinstance(seller_low.payload.get("error"), str)
            and not contains_key(seller_low.payload, {"items", "product", "products"}),
            expected="HTTP 403 JSON y ningún listado",
            actual=observation_summary(seller_low),
            probable_file="app/services/chatbot_service.py",
            priority="CRÍTICA",
        )
    )

    inventory_low = send(
        "inventario",
        inventario,
        message="Productos bajo stock",
    )
    permission_results.append(
        runner.check(
            "P06",
            "Inventario low_stock -> 200 ok/empty",
            conversation_matches(
                inventory_low,
                "low_stock_products",
                {"ok", "empty"},
            ),
            expected="HTTP 200 status ok o empty",
            actual=observation_summary(inventory_low),
            probable_file="app/services/chatbot_service.py",
        )
    )

    page_controls_ok = (
        'data-chat-prompt="Stock exacto de"' in chatbot_pages["admin"].text
        and 'data-chat-prompt="Productos bajo stock"' in chatbot_pages["admin"].text
        and 'data-chat-prompt="Stock exacto de"' in chatbot_pages["inventario"].text
        and 'data-chat-prompt="Productos bajo stock"' in chatbot_pages["inventario"].text
        and 'data-chat-prompt="Stock exacto de"' not in chatbot_pages["vendedor"].text
        and 'data-chat-prompt="Productos bajo stock"' not in chatbot_pages["vendedor"].text
    )
    runner.check(
        "P09",
        "Frontend oculta controles de stock al vendedor",
        page_controls_ok,
        expected="Controles visibles para admin/inventario y ausentes para vendedor",
        actual=(
            f"admin={chatbot_pages['admin'].status_code}, "
            f"inventario={chatbot_pages['inventario'].status_code}, "
            f"vendedor={chatbot_pages['vendedor'].status_code}"
        ),
        probable_file="app/templates/chatbot.html / app/routes/pages.py",
        priority="ALTA",
    )

    runner.group("Matriz completa de intenciones admin")

    product_name_for_matrix = (
        str(selected.get("name") or "") if selected else "objeto inexistente alfa"
    )
    product_code_for_matrix = (
        str(selected.get("code") or "") if selected else missing_code
    )
    category_for_matrix = (
        str(selected.get("category") or "")
        if selected
        else "categoria inexistente alfa"
    )
    expected_product_status = {"ok"} if selected else {"not_found"}
    matrix_specs = [
        ("A01", "greeting", "Hola", {"ok"}),
        ("A02", "help", "Ayuda", {"ok"}),
        (
            "A03",
            "product_by_name",
            f"Buscar producto {product_name_for_matrix}",
            expected_product_status,
        ),
        (
            "A04",
            "product_by_code",
            f"Código {product_code_for_matrix}",
            expected_product_status,
        ),
        (
            "A05",
            "product_stock",
            f"Stock exacto de {product_name_for_matrix}",
            expected_product_status,
        ),
        (
            "A06",
            "product_price",
            f"Precio de {product_name_for_matrix}",
            expected_product_status,
        ),
        (
            "A07",
            "product_category",
            f"Categoría de {product_name_for_matrix}",
            expected_product_status,
        ),
        (
            "A08",
            "products_by_category",
            f"Productos de la categoría {category_for_matrix}",
            {"ok", "empty"} if selected else {"not_found"},
        ),
        (
            "A09",
            "low_stock_products",
            "Productos bajo stock",
            {"ok", "empty"},
        ),
        (
            "A10",
            "out_of_stock_products",
            "Productos agotados",
            {"ok", "empty"},
        ),
        (
            "A11",
            "product_status",
            f"Estado de {product_name_for_matrix}",
            expected_product_status,
        ),
        ("A12", "unknown", "xyzzy quux", {"unknown"}),
    ]
    matrix_ok: list[bool] = []
    for case_id, intent, message, statuses in matrix_specs:
        observation = send("admin", admin, message=message)
        condition = conversation_matches(observation, intent, statuses)
        matrix_ok.append(condition)
        runner.check(
            case_id,
            f"Admin puede consultar {intent}",
            condition,
            expected=f"HTTP 200 intent={intent}, status en {sorted(statuses)}",
            actual=observation_summary(observation),
            probable_file="app/services/chatbot_service.py",
            priority="ALTA",
        )
    runner.check(
        "P08",
        "Admin puede consultar todas las intenciones",
        all(matrix_ok) and len(matrix_ok) == len(matrix_specs),
        expected="12/12 intenciones con HTTP 200 y estado semántico válido",
        actual=f"{sum(matrix_ok)}/{len(matrix_specs)} válidas",
        probable_file="app/services/chatbot_service.py",
        priority="ALTA",
    )

    runner.group("Activos, exactitud y ranking")

    if not inactive_products:
        runner.skip(
            "P10",
            "Código de producto inactivo queda excluido",
            "No existe producto inactivo; se valida el predicado ORM en P12.",
        )
        runner.skip(
            "P11",
            "Nombre de producto inactivo queda excluido",
            "No existe producto inactivo; se valida el predicado ORM en P12.",
        )
    else:
        inactive = inactive_products[0]
        inactive_code_response = send(
            "admin",
            admin,
            message=f"Código {inactive.get('code')}",
        )
        inactive_code_product = response_product(inactive_code_response.payload)
        runner.check(
            "P10",
            "Código de producto inactivo queda excluido",
            conversation_matches(
                inactive_code_response,
                "product_by_code",
                {"not_found"},
            )
            and inactive_code_product is None,
            expected="HTTP 200 status=not_found; nunca devuelve el ID inactivo",
            actual=observation_summary(inactive_code_response),
            probable_file="app/services/chatbot_service.py",
            priority="ALTA",
        )

        inactive_for_name: dict[str, Any] | None = None
        with app.app_context():
            for candidate in inactive_products:
                lookup = chatbot_service.find_product(
                    str(candidate.get("name") or "")
                )
                if lookup.product is None and not lookup.candidates:
                    inactive_for_name = candidate
                    break
        if inactive_for_name is None:
            runner.skip(
                "P11",
                "Nombre de producto inactivo queda excluido",
                "Los nombres inactivos existentes colisionan con activos; P10/P12 cubren exclusión.",
            )
        else:
            inactive_name_response = send(
                "admin",
                admin,
                message=f"Buscar producto {inactive_for_name.get('name')}",
            )
            runner.check(
                "P11",
                "Nombre de producto inactivo queda excluido",
                conversation_matches(
                    inactive_name_response,
                    "product_by_name",
                    {"not_found"},
                )
                and response_product(inactive_name_response.payload) is None,
                expected="HTTP 200 status=not_found para nombre inactivo existente",
                actual=observation_summary(inactive_name_response),
                probable_file="app/services/chatbot_service.py",
                priority="ALTA",
            )

    try:
        with app.app_context():
            active_query = chatbot_service._active_products_query()  # noqa: SLF001
            compiled_query = str(
                active_query.statement.compile(
                    dialect=db.engine.dialect,
                    compile_kwargs={"literal_binds": True},
                )
            ).lower()
            orm_products = chatbot_service._active_products()  # noqa: SLF001
            orm_active_ids = {product.id for product in orm_products}
            inactive_ids = {
                int(product.get("id"))
                for product in inactive_products
                if product.get("id") is not None
            }
        active_predicate_ok = (
            "is_active" in compiled_query
            and (" is true" in compiled_query or " = true" in compiled_query)
            and not (orm_active_ids & inactive_ids)
            and all(product.is_active is True for product in orm_products)
        )
        active_predicate_actual = (
            f"ORM activos={len(orm_active_ids)}, inactivos intersectados="
            f"{len(orm_active_ids & inactive_ids)}"
        )
    except Exception as error:  # noqa: BLE001
        active_predicate_ok = False
        active_predicate_actual = type(error).__name__
    runner.check(
        "P12",
        "Consulta ORM aplica is_active=True",
        active_predicate_ok,
        expected="Predicado SQL explícito y cero IDs inactivos",
        actual=active_predicate_actual,
        probable_file="app/services/chatbot_service.py",
        priority="ALTA",
    )

    image_helper_ok = (
        chatbot_service.safe_product_image_url("https://evil.invalid/x.png") is None
        and chatbot_service.safe_product_image_url("uploads/products/x.png") is None
        and chatbot_service.safe_product_image_url(r"C:\tmp\x.png") is None
        and chatbot_service.safe_product_image_url(SAFE_IMAGE_PATH) == SAFE_IMAGE_PATH
    )
    runner.check(
        "P13",
        "image_url externa es null e interna válida se conserva",
        image_helper_ok,
        expected="externas/físicas/uploads -> None; /media/products/*.png -> igual",
        actual=f"helper_valid={image_helper_ok}",
        probable_file="app/services/chatbot_service.py",
        priority="CRÍTICA",
    )

    if selected is None:
        runner.skip("P14", "Código no usa fuzzy", "No hay producto activo existente.")
        runner.skip(
            "P15",
            "Nombre de al menos 4 caracteres usa aproximación segura",
            "No hay producto activo existente.",
        )
        runner.skip(
            "P16",
            "Saludo más precio prioriza product_price",
            "No hay producto activo existente.",
        )
    else:
        code_typo = make_code_typo(str(selected.get("code") or ""), codes)
        exact_code_response = send(
            "admin",
            admin,
            message=f"Código {code_typo}",
        )
        runner.check(
            "P14",
            "Código exacto no usa fuzzy",
            conversation_matches(
                exact_code_response,
                "product_by_code",
                {"not_found"},
            )
            and response_product(exact_code_response.payload) is None,
            expected="Un código similar pero inexistente produce not_found",
            actual=observation_summary(exact_code_response),
            probable_file="app/services/chatbot_service.py",
            priority="ALTA",
        )

        fuzzy_case = find_fuzzy_case(app, active_products)
        if fuzzy_case is None:
            runner.skip(
                "P15",
                "Nombre de al menos 4 caracteres usa aproximación segura",
                "Los nombres activos actuales no producen un typo inequívoco sobre el umbral.",
            )
        else:
            fuzzy_product, fuzzy_query, fuzzy_ratio = fuzzy_case
            fuzzy_response = send(
                "admin",
                admin,
                message=f"Buscar producto {fuzzy_query}",
            )
            fuzzy_result = response_product(fuzzy_response.payload)
            runner.check(
                "P15",
                "Nombre de al menos 4 caracteres usa aproximación segura",
                conversation_matches(
                    fuzzy_response,
                    "product_by_name",
                    {"ok"},
                )
                and fuzzy_result is not None
                and fuzzy_result.get("id") == fuzzy_product.get("id")
                and len(chatbot_service.normalize_text(fuzzy_query))
                >= chatbot_service.FUZZY_MIN_LENGTH,
                expected=(
                    "HTTP 200 para typo inequívoco, longitud mínima y umbral "
                    f">={chatbot_service.FUZZY_THRESHOLD}"
                ),
                actual=(
                    f"ratio={fuzzy_ratio:.3f}, "
                    + observation_summary(fuzzy_response)
                ),
                probable_file="app/services/chatbot_service.py",
            )

        greeting_price = send(
            "admin",
            admin,
            message=f"Hola, precio de {selected.get('name')}",
        )
        runner.check(
            "P16",
            "Saludo más precio prioriza product_price",
            conversation_matches(greeting_price, "product_price", {"ok"})
            and response_product(greeting_price.payload) is not None,
            expected="HTTP 200 intent=product_price, no greeting",
            actual=observation_summary(greeting_price),
            probable_file="app/services/chatbot_service.py",
        )

    fake_ambiguous_ok, fake_ambiguous_actual, _ = fake_ambiguity_contract()
    runner.check(
        "P17",
        "Ranking ambiguo funciona con objetos simulados sin BD",
        fake_ambiguous_ok,
        expected="Dos candidatos y needs_clarification",
        actual=fake_ambiguous_actual,
        probable_file="app/services/chatbot_service.py",
    )

    runner.group("Validación HTTP y error genérico")

    wrong_content_type = send(
        "admin",
        admin,
        raw_body=json.dumps({"message": "Hola"}),
        headers={"Content-Type": "text/plain", "Accept": "application/json"},
    )
    runner.check(
        "V01",
        "Content-Type distinto de JSON -> 400 JSON",
        is_json_status(wrong_content_type, 400),
        expected="HTTP 400 application/json",
        actual=observation_summary(wrong_content_type),
        probable_file="app/controllers/chatbot_controller.py",
        priority="ALTA",
    )

    numeric_message = send("admin", admin, json_body={"message": 123})
    runner.check(
        "V02",
        "message numérico -> 400 JSON",
        is_json_status(numeric_message, 400),
        expected="HTTP 400 application/json",
        actual=observation_summary(numeric_message),
        probable_file="app/controllers/chatbot_controller.py",
    )

    null_message = send("admin", admin, json_body={"message": None})
    runner.check(
        "V03",
        "message null -> 400 JSON",
        is_json_status(null_message, 400),
        expected="HTTP 400 application/json",
        actual=observation_summary(null_message),
        probable_file="app/controllers/chatbot_controller.py",
    )

    array_body = send("admin", admin, json_body=["Hola"])
    runner.check(
        "V04",
        "Body JSON no objeto -> 400 JSON",
        is_json_status(array_body, 400),
        expected="HTTP 400 application/json",
        actual=observation_summary(array_body),
        probable_file="app/controllers/chatbot_controller.py",
    )

    malformed_json = send(
        "admin",
        admin,
        raw_body='{"message":',
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    runner.check(
        "V05",
        "JSON malformado -> 400 JSON",
        is_json_status(malformed_json, 400),
        expected="HTTP 400 application/json",
        actual=observation_summary(malformed_json),
        probable_file="app/controllers/chatbot_controller.py",
        priority="ALTA",
    )

    missing_message = send("admin", admin, json_body={})
    runner.check(
        "V06",
        "Campo message ausente -> 400 JSON",
        is_json_status(missing_message, 400),
        expected="HTTP 400 application/json",
        actual=observation_summary(missing_message),
        probable_file="app/controllers/chatbot_controller.py",
    )

    admin_user_id = (
        int(me_initial.payload.get("id"))
        if isinstance(me_initial.payload, dict) and me_initial.payload.get("id") is not None
        else 0
    )
    if admin_user_id <= 0:
        runner.skip(
            "V07",
            "Excepción interna -> 500 genérico",
            "GET /api/auth/me no entregó un ID autenticado.",
        )
        runner.skip(
            "V08",
            "500 no filtra trazas",
            "GET /api/auth/me no entregó un ID autenticado.",
        )
    else:
        generic_500 = generic_500_observation(app, admin_user_id)
        http.record_test_client("POST", "/api/chatbot/message")
        chatbot_observations.append(("admin_monkeypatch", generic_500))
        runner.check(
            "V07",
            "Excepción interna -> 500 genérico",
            is_json_status(generic_500, 500)
            and generic_500.payload == {"error": "Error interno del servidor."},
            expected='HTTP 500 JSON exacto {"error":"Error interno del servidor."}',
            actual=observation_summary(generic_500),
            probable_file="app/controllers/chatbot_controller.py",
            priority="CRÍTICA",
        )
        runner.check(
            "V08",
            "500 no filtra trazas",
            "SENSITIVE_INTERNAL_MARKER" not in generic_500.text
            and not TRACE_RE.search(generic_500.text),
            expected="Sin marcador interno, traceback, SQLAlchemy ni rutas de archivo",
            actual=f"body_keys={sorted(generic_500.payload) if isinstance(generic_500.payload, dict) else []}",
            probable_file="app/controllers/chatbot_controller.py",
            priority="CRÍTICA",
        )

    runner.group("Seguridad transversal de respuestas")

    all_violations: list[str] = []
    trace_failures: list[str] = []
    for role_label, observation in chatbot_observations:
        target = observation.payload if observation.payload is not None else observation.text
        for violation in security_violations(target):
            all_violations.append(f"{role_label}:{violation}")
        if TRACE_RE.search(observation.text):
            trace_failures.append(f"{role_label}:{observation.status_code}")
    runner.check(
        "X01",
        "Ninguna respuesta del chatbot filtra campos/rutas/URLs prohibidas",
        not all_violations,
        expected=(
            "Sin purchase_price, password_hash, ruta física, uploads/ "
            "ni URL externa"
        ),
        actual=(
            f"violaciones={sorted(set(all_violations))}"
            if all_violations
            else "violaciones=[]"
        ),
        probable_file="app/services/chatbot_service.py",
        priority="CRÍTICA",
    )
    runner.check(
        "X02",
        "Ninguna respuesta contiene trazas internas",
        not trace_failures,
        expected="Cero trazas o nombres de infraestructura",
        actual=f"respuestas_con_traza={trace_failures}",
        probable_file="app/controllers/chatbot_controller.py",
        priority="CRÍTICA",
    )

    runner.group("Contrato estático backend/frontend")

    service_readonly, service_forbidden_calls = persistence_calls_absent(
        sources["service"]
    )
    script_readonly, script_forbidden_calls = persistence_calls_absent(
        sources["test"]
    )
    runner.check(
        "S01",
        "Ruta API exacta y protegida por products:read",
        'url_prefix="/api/chatbot"' in sources["route"]
        and '"/message"' in sources["route"]
        and "permission_required(PRODUCTS_READ)" in sources["route"]
        and 'methods=["POST"]' in sources["route"],
        expected="POST /api/chatbot/message con permission_required(PRODUCTS_READ)",
        actual="contrato estático de app/routes/chatbot.py",
        probable_file="app/routes/chatbot.py",
        priority="ALTA",
    )
    runner.check(
        "S02",
        "Controlador valida JSON, tipo, longitud y captura 500",
        'request.mimetype != "application/json"' in sources["controller"]
        and "MAX_MESSAGE_LENGTH = 500" in sources["controller"]
        and "not isinstance(message, str)" in sources["controller"]
        and "except Exception" in sources["controller"]
        and '"Error interno del servidor."' in sources["controller"],
        expected="Todas las defensas HTTP presentes",
        actual="aserción AST/textual del controlador",
        probable_file="app/controllers/chatbot_controller.py",
        priority="ALTA",
    )
    runner.check(
        "S03",
        "Servicio del chatbot no contiene persistencia",
        service_readonly
        and "_active_products_query" in sources["service"]
        and "Product.is_active.is_(True)" in sources["service"],
        expected="Cero llamadas persistentes y filtro activo explícito",
        actual=f"llamadas_prohibidas={service_forbidden_calls}",
        probable_file="app/services/chatbot_service.py",
        priority="CRÍTICA",
    )
    runner.check(
        "S05",
        "Título por rol y soporte sin información comercial",
        'ROLE_INVENTARIO: "Asistente de Inventario"' in sources["service"]
        and 'ROLE_ASISTENTE: "Asistente Administrativo"' in sources["service"]
        and 'ROLE_SOPORTE_TI: "Asistente de Soporte"' in sources["service"]
        and "No consulto precios, costos, proveedores ni operaciones comerciales." in sources["service"]
        and 'intent == INTENT_PRODUCT_PRICE and not _sees_sale_price(role)' in sources["service"],
        expected="Títulos por rol, soporte sin datos comerciales e inventario sin precio",
        actual="aserción estática de app/services/chatbot_service.py",
        probable_file="app/services/chatbot_service.py",
        priority="ALTA",
    )
    runner.check(
        "S04",
        "Frontend usa endpoint/JSON seguro y no innerHTML",
        'const ENDPOINT = "/api/chatbot/message"' in sources["js"]
        and 'credentials: "same-origin"' in sources["js"]
        and '"Content-Type": "application/json"' in sources["js"]
        and ".innerHTML" not in sources["js"]
        and "textContent" in sources["js"]
        and "SAFE_IMAGE_RE" in sources["js"],
        expected="fetch same-origin JSON, textContent y lista blanca de imagen",
        actual="aserción estática de app/static/js/chatbot.js",
        probable_file="app/static/js/chatbot.js",
        priority="ALTA",
    )
    runner.check(
        "S05",
        "Template declara solo consulta, máximo 500 y controles por rol",
        "Solo consulta" in sources["template"]
        and 'maxlength="500"' in sources["template"]
        and "{% if can_view_stock %}" in sources["template"]
        and "Este asistente no crea ni modifica" in sources["template"],
        expected="Badge solo consulta, maxlength y condición can_view_stock",
        actual="aserción estática de app/templates/chatbot.html",
        probable_file="app/templates/chatbot.html",
    )
    runner.check(
        "S06",
        "Frontend responsive y reduced-motion",
        "@media (max-width: 991.98px)" in sources["css"]
        and "@media (max-width: 575.98px)" in sources["css"]
        and "@media (prefers-reduced-motion: reduce)" in sources["css"],
        expected="Breakpoints y prefers-reduced-motion presentes",
        actual="aserción estática de app/static/css/chatbot.css",
        probable_file="app/static/css/chatbot.css",
    )
    runner.check(
        "S07",
        "Página, sidebar y blueprint están registrados",
        '@pages_bp.get("/chatbot")' in sources["pages"]
        and "@login_required" in sources["pages"]
        and 'href="/chatbot"' in sources["base_template"]
        and "app.register_blueprint(chatbot_bp)" in sources["app_init"],
        expected="Ruta HTML, enlace y registro del blueprint",
        actual="aserción estática de rutas/templates/factory",
        probable_file="app/routes/pages.py / app/__init__.py",
    )

    counter_output_match = re.search(
        r'<output\b[^>]*id="chatbot-counter"[^>]*>\s*0/500\s*</output>',
        sources["template"],
        re.DOTALL,
    )
    counter_output_tag = (
        counter_output_match.group(0) if counter_output_match else ""
    )
    counter_alert_match = re.search(
        r'<span\b[^>]*id="chatbot-counter-alert"[^>]*>',
        sources["template"],
        re.DOTALL,
    )
    counter_alert_tag = (
        counter_alert_match.group(0) if counter_alert_match else ""
    )
    typing_tag_match = re.search(
        r'<div\b[^>]*class="chatbot-typing"[^>]*>',
        sources["template"],
        re.DOTALL,
    )
    typing_tag = typing_tag_match.group(0) if typing_tag_match else ""
    role_log_count = sources["template"].count('role="log"')

    runner.check(
        "S08",
        "Categorías ambiguas se renderizan como opciones, no product cards",
        '"description": category.description' in sources["service"]
        and 'data={"count": len(categories), "categories": categories}'
        in sources["service"]
        and "function responseCategories(response)" in sources["js"]
        and "Array.isArray(data.categories)" in sources["js"]
        and ".slice(0, 5)" in sources["js"]
        and "function createCategoryOptions(categories)" in sources["js"]
        and "if (isCategoryClarification(response)) return [];" in sources["js"],
        expected=(
            "Backend data.categories y frontend de opciones limitado a 5, "
            "sin tarjetas de producto"
        ),
        actual="aserción estática de servicio y frontend",
        probable_file=(
            "app/services/chatbot_service.py / app/static/js/chatbot.js"
        ),
        priority="ALTA",
    )
    runner.check(
        "S09",
        "Contador 0/500 anuncia solo cambios de umbral",
        bool(counter_output_tag)
        and "aria-live" not in counter_output_tag
        and bool(counter_alert_tag)
        and 'aria-live="polite"' in counter_alert_tag
        and 'aria-atomic="true"' in counter_alert_tag
        and "length >= 450" in sources["js"]
        and "length >= MAX_MESSAGE_LENGTH" in sources["js"]
        and "if (threshold === state.counterThreshold) return;" in sources["js"]
        and 'threshold === "warning"' in sources["js"]
        and 'threshold === "limit"' in sources["js"],
        expected=(
            "Output visual sin aria-live; región oculta anuncia transiciones "
            "450/500, no cada pulsación"
        ),
        actual=(
            f"counter_found={bool(counter_output_tag)}, "
            f"counter_live={'aria-live' in counter_output_tag}, "
            f"alert_found={bool(counter_alert_tag)}"
        ),
        probable_file="app/templates/chatbot.html / app/static/js/chatbot.js",
        priority="ALTA",
    )
    runner.check(
        "S10",
        "Conversación usa un solo canal live",
        role_log_count == 1
        and re.search(
            r'<div\b[^>]*id="chatbot-log"[^>]*aria-live="polite"',
            sources["template"],
            re.DOTALL,
        )
        is not None
        and bool(typing_tag)
        and "aria-live" not in typing_tag
        and 'role="status"' not in typing_tag
        and 'role="alert"' not in sources["template"],
        expected=(
            "Un role=log live para mensajes; typing/error sin canales "
            "competidores"
        ),
        actual=(
            f"role_log={role_log_count}, "
            f"typing_live={'aria-live' in typing_tag}"
        ),
        probable_file="app/templates/chatbot.html",
        priority="ALTA",
    )
    runner.check(
        "S11",
        "Foco contrastado y autoscroll al inicio del mensaje",
        "--chatbot-focus: #005ea8" in sources["css"]
        and ".chatbot-category-option:focus-visible" in sources["css"]
        and "outline: 3px solid var(--chatbot-focus)" in sources["css"]
        and "box-shadow: 0 0 0 1px #fff" in sources["css"]
        and 'function revealLogNode(node, alignment = "start")' in sources["js"]
        and 'top: alignment === "end" ? end : start' in sources["js"]
        and "insertBeforeTyping(rendered.message, {" in sources["js"]
        and "reveal: !state.reviewingHistory" in sources["js"],
        expected=(
            "Outline visible y mensajes del asistente revelados desde su inicio "
            "sin desplazar historial revisado"
        ),
        actual="aserción estática CSS/JS",
        probable_file="app/static/css/chatbot.css / app/static/js/chatbot.js",
        priority="ALTA",
    )
    runner.check(
        "S12",
        "Reduced-motion y layout móvil evitan conflictos",
        'behavior: prefersReducedMotion() ? "auto" : "smooth"' in sources["js"]
        and "@media (prefers-reduced-motion: reduce)" in sources["css"]
        and "animation: none" in sources["css"]
        and "transition: none" in sources["css"]
        and "@media (max-width: 767.98px)" in sources["css"]
        and "height: clamp(18rem, 62vh, 30rem)" in sources["css"]
        and (
            "@media (max-width: 991.98px) and (max-height: 600px) "
            "and (orientation: landscape)"
        )
        in sources["css"]
        and "height: clamp(14rem, 55vh, 20rem)" in sources["css"],
        expected=(
            "Scroll instantáneo con reduced-motion y alturas móviles/landscape "
            "sin doble restricción"
        ),
        actual="aserción estática CSS/JS",
        probable_file="app/static/css/chatbot.css / app/static/js/chatbot.js",
        priority="ALTA",
    )

    typography_selectors = (
        ".chatbot-message__author",
        ".chatbot-response-state",
        ".chatbot-category-option__description",
        ".chatbot-results__summary",
        ".chatbot-product-card__code",
        ".chatbot-product-card__description",
        ".chatbot-product-card__fact span",
        ".chatbot-availability",
        ".chatbot-response-actions__title",
        ".chatbot-suggestion",
        ".chatbot-input-error",
        ".chatbot-input-help",
    )
    typography_sizes = {
        selector: css_property_rem(sources["css"], selector, "font-size")
        for selector in typography_selectors
    }
    runner.check(
        "S13",
        "Textos funcionales relevantes son de al menos 0.75rem",
        all(
            size is not None and size >= 0.75
            for size in typography_sizes.values()
        ),
        expected="Todos los selectores críticos con font-size >= 0.75rem",
        actual=f"font_sizes={typography_sizes}",
        probable_file="app/static/css/chatbot.css",
        priority="MEDIA",
    )

    frontend_sources = sources["js"] + "\n" + sources["template"]
    forbidden_frontend_tokens = [
        token
        for token in (
            ".innerHTML",
            "localStorage",
            "sessionStorage",
            "purchase_price",
        )
        if token in frontend_sources
    ]
    runner.check(
        "S14",
        "Frontend no usa HTML inseguro, storage ni purchase_price",
        not forbidden_frontend_tokens
        and sources["template"].count("Solo consulta") >= 2,
        expected=(
            "Sin innerHTML/localStorage/sessionStorage/purchase_price y "
            "modo Solo consulta visible"
        ),
        actual=(
            f"tokens_prohibidos={forbidden_frontend_tokens}, "
            f"solo_consulta={sources['template'].count('Solo consulta')}"
        ),
        probable_file="app/static/js/chatbot.js / app/templates/chatbot.html",
        priority="CRÍTICA",
    )

    runner.group("Regresión HTTP de páginas y APIs")

    regression_pages = {
        path: http.request(admin, "GET", path)
        for path in (
            "/dashboard",
            "/products",
            "/categories",
            "/inventory",
            "/delivery-notes",
            "/catalog",
            "/profile",
        )
    }
    regression_login = http.request(
        http.anonymous,
        "GET",
        "/login",
        allow_redirects=False,
    )
    regression_apis = {
        path: http.request(admin, "GET", path)
        for path in (
            "/api/products",
            "/api/categories",
            "/api/inventory/movements",
            "/api/delivery-notes",
            "/api/reports/dashboard-summary",
            "/api/auth/me",
        )
    }

    runner.check(
        "R01",
        "/categories sigue siendo HTML 200",
        regression_pages["/categories"].status_code == 200
        and regression_pages["/categories"].content_type.startswith("text/html"),
        expected="HTTP 200 text/html",
        actual=observation_summary(regression_pages["/categories"]),
        probable_file="app/routes/pages.py",
    )
    for case_id, path, required_keys in (
        ("R02", "/api/products", {"count", "items"}),
        ("R03", "/api/categories", {"count", "items"}),
        ("R04", "/api/inventory/movements", {"count", "items"}),
        ("R05", "/api/delivery-notes", {"count", "items"}),
        ("R06", "/api/reports/dashboard-summary", set()),
        ("R07", "/api/auth/me", {"id", "role"}),
    ):
        observation = regression_apis[path]
        condition = (
            is_json_status(observation, 200)
            and isinstance(observation.payload, dict)
            and required_keys.issubset(observation.payload)
        )
        runner.check(
            case_id,
            f"{path} sigue siendo JSON 200",
            condition,
            expected=f"HTTP 200 application/json con claves {sorted(required_keys)}",
            actual=observation_summary(observation),
            probable_file=(
                "app/routes/reports.py"
                if "/reports/" in path
                else "app/routes / app/controllers"
            ),
            priority="ALTA",
        )
    runner.check(
        "R08",
        "Regresión conjunta no produce HTTP 500",
        all(
            observation.status_code != 500
            for observation in (
                list(regression_pages.values())
                + [regression_login]
                + list(regression_apis.values())
            )
        ),
        expected="Cero respuestas HTTP 500",
        actual=(
            "statuses="
            + str(
                {
                    **{
                        path: observation.status_code
                        for path, observation in regression_pages.items()
                    },
                    "/login": regression_login.status_code,
                    **{
                        path: observation.status_code
                        for path, observation in regression_apis.items()
                    },
                }
            )
        ),
        probable_file="app/routes / app/controllers",
        priority="ALTA",
    )

    try:
        final_snapshot = take_snapshot(app)
        final_snapshot_error = ""
    except Exception as error:  # noqa: BLE001
        final_snapshot = None
        final_snapshot_error = type(error).__name__

    runner.group("Base aprobada B22-B34: inmutabilidad y regresión")

    stock_unchanged = bool(
        final_snapshot is not None
        and baseline.stock_total == final_snapshot.stock_total
        and baseline.stock_by_product == final_snapshot.stock_by_product
    )
    runner.check(
        "B22",
        "Stock total y por producto permanecen inmutables",
        stock_unchanged,
        expected=(
            f"total={baseline.stock_total} y "
            f"{len(baseline.stock_by_product)} pares id/stock idénticos"
        ),
        actual=(
            f"snapshot_error={final_snapshot_error}"
            if final_snapshot is None
            else (
                f"total {baseline.stock_total}->{final_snapshot.stock_total}, "
                f"por_producto="
                f"{'igual' if baseline.stock_by_product == final_snapshot.stock_by_product else 'cambió'}"
            )
        ),
        probable_file="app/services/chatbot_service.py",
        priority="CRÍTICA",
    )
    runner.check(
        "B23",
        "stock_movements permanece inmutable",
        bool(
            final_snapshot is not None
            and table_unchanged(baseline, final_snapshot, "stock_movements")
        ),
        expected="Mismos valores, IDs y conteo",
        actual=snapshot_actual(
            baseline,
            final_snapshot,
            ["stock_movements"],
        ),
        probable_file="app/services/chatbot_service.py",
        priority="CRÍTICA",
    )
    runner.check(
        "B24",
        "delivery_notes y delivery_note_items permanecen inmutables",
        bool(
            final_snapshot is not None
            and table_unchanged(baseline, final_snapshot, "delivery_notes")
            and table_unchanged(baseline, final_snapshot, "delivery_note_items")
        ),
        expected="Mismos valores, IDs y conteos en ambas tablas",
        actual=snapshot_actual(
            baseline,
            final_snapshot,
            ["delivery_notes", "delivery_note_items"],
        ),
        probable_file="app/services/chatbot_service.py",
        priority="CRÍTICA",
    )
    runner.check(
        "B25",
        "products permanece inmutable",
        bool(
            final_snapshot is not None
            and table_unchanged(baseline, final_snapshot, "products")
        ),
        expected="Mismos valores, IDs y conteo",
        actual=snapshot_actual(baseline, final_snapshot, ["products"]),
        probable_file="app/services/chatbot_service.py",
        priority="CRÍTICA",
    )
    runner.check(
        "B26",
        "categories permanece inmutable",
        bool(
            final_snapshot is not None
            and table_unchanged(baseline, final_snapshot, "categories")
        ),
        expected="Mismos valores, IDs y conteo",
        actual=snapshot_actual(baseline, final_snapshot, ["categories"]),
        probable_file="app/services/chatbot_service.py",
        priority="CRÍTICA",
    )

    non_json_chatbot = [
        f"{role}:{observation.status_code}:{observation.content_type}"
        for role, observation in chatbot_observations
        if not observation.is_json
    ]
    runner.check(
        "B27",
        "Todas las respuestas del chatbot son JSON",
        not non_json_chatbot and bool(chatbot_observations),
        expected="application/json en éxitos, validaciones, 401, 403 y 500",
        actual=f"no_json={non_json_chatbot}, total={len(chatbot_observations)}",
        probable_file="app/controllers/chatbot_controller.py / app/__init__.py",
        priority="CRÍTICA",
    )
    for case_id, path, title in (
        ("B28", "/dashboard", "Dashboard"),
        ("B29", "/products", "Productos"),
        ("B30", "/inventory", "Inventario"),
        ("B31", "/delivery-notes", "Notas de entrega"),
        ("B32", "/catalog", "Catálogo"),
    ):
        observation = regression_pages[path]
        runner.check(
            case_id,
            f"{title} mantiene HTTP 200 HTML",
            observation.status_code == 200
            and observation.content_type.startswith("text/html"),
            expected="HTTP 200 text/html",
            actual=observation_summary(observation),
            probable_file="app/routes/pages.py",
            priority="ALTA",
        )

    profile_observation = regression_pages["/profile"]
    auth_me_observation = regression_apis["/api/auth/me"]
    runner.check(
        "B33",
        "Profile y autenticación mantienen contrato",
        profile_observation.status_code == 200
        and profile_observation.content_type.startswith("text/html")
        and regression_login.status_code == 200
        and regression_login.content_type.startswith("text/html")
        and is_json_status(auth_me_observation, 200)
        and not contains_key(auth_me_observation.payload, {"password_hash"}),
        expected="/profile y /login HTML 200; /api/auth/me JSON 200 sin hash",
        actual=(
            f"profile={profile_observation.status_code}, "
            f"login={regression_login.status_code}, "
            f"auth_me={auth_me_observation.status_code}"
        ),
        probable_file="app/routes/pages.py / app/controllers/auth_controller.py",
        priority="ALTA",
    )

    operational_writes = http.operational_writes()
    immutable_everything = bool(
        final_snapshot is not None
        and all_tables_unchanged(baseline, final_snapshot)
    )
    runner.check(
        "B34",
        "Sin creación de datos de prueba ni escritura operativa",
        immutable_everything
        and not operational_writes
        and script_readonly,
        expected=(
            "Todas las tablas idénticas, cero endpoint operativo de escritura "
            "y cero llamada persistente en el script"
        ),
        actual=(
            f"tables_equal={immutable_everything}, "
            f"operational_writes={operational_writes}, "
            f"script_calls={script_forbidden_calls}"
        ),
        probable_file="scripts/test_chatbot.py",
        priority="CRÍTICA",
    )

    runner.group("Snapshots obligatorios y guardas no mutantes")

    runner.check(
        "N01",
        "users mantiene valores, IDs y conteo",
        bool(
            final_snapshot is not None
            and table_unchanged(baseline, final_snapshot, "users")
        ),
        expected="Misma huella canónica, IDs y conteo",
        actual=snapshot_actual(baseline, final_snapshot, ["users"]),
        probable_file="scripts/test_chatbot.py",
        priority="CRÍTICA",
    )
    runner.check(
        "N02",
        "Snapshot integral antes/después es idéntico",
        immutable_everything,
        expected=(
            "products, categories, stock_movements, delivery_notes, "
            "delivery_note_items y users idénticos"
        ),
        actual=(
            "snapshot final no disponible"
            if final_snapshot is None
            else "; ".join(
                f"{name}={'igual' if baseline.tables[name] == final_snapshot.tables[name] else 'cambió'}"
                for name in baseline.tables
            )
        ),
        probable_file="scripts/test_chatbot.py",
        priority="CRÍTICA",
    )
    runner.check(
        "N03",
        "Ledger confirma cero endpoint operativo de escritura",
        not operational_writes,
        expected=(
            "Solo GET, POST /api/auth/login y POST /api/chatbot/message"
        ),
        actual=f"operational_writes={operational_writes}",
        probable_file="scripts/test_chatbot.py",
        priority="CRÍTICA",
    )
    runner.check(
        "N04",
        "AST del script confirma ausencia de persistencia",
        script_readonly,
        expected="Cero llamada de alta, edición, borrado o confirmación",
        actual=f"llamadas_prohibidas={script_forbidden_calls}",
        probable_file="scripts/test_chatbot.py",
        priority="CRÍTICA",
    )

    runner.group("Integridad de cobertura")
    missing_base = sorted(BASE_CASE_IDS - runner.ids)
    runner.check(
        "Q01",
        "Están numeradas las 34 pruebas base",
        not missing_base,
        expected="B01..B34 presentes",
        actual=f"faltantes={missing_base}",
        probable_file="scripts/test_chatbot.py",
        priority="ALTA",
    )
    missing_extra = sorted(MANDATORY_EXTRA_IDS - runner.ids)
    runner.check(
        "Q02",
        "Están presentes las pruebas adicionales obligatorias",
        not missing_extra,
        expected="Permisos, validación, seguridad y snapshots presentes",
        actual=f"faltantes={missing_extra}",
        probable_file="scripts/test_chatbot.py",
        priority="ALTA",
    )

    if final_snapshot is not None:
        print(
            "\nSNAPSHOT: "
            f"stock_total {baseline.stock_total}->{final_snapshot.stock_total}; "
            + ", ".join(
                f"{name} {state.count}->{final_snapshot.tables[name].count}"
                for name, state in baseline.tables.items()
            )
        )
    print(
        "HTTP LEDGER: "
        f"calls={len(http.entries)}, operational_writes={len(operational_writes)}"
    )
    return runner.report()


if __name__ == "__main__":
    raise SystemExit(main())
