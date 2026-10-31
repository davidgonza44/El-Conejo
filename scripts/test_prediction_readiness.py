#!/usr/bin/env python3
"""Pruebas del diagnóstico de suficiencia histórica (prediction readiness).

Suite aislada: SQLite temporal + Flask test_client. No usa el MySQL de
desarrollo para las 48 verificaciones, no deja datos TEST permanentes y no
entrena modelos ni genera pronósticos.

Uso: python scripts/test_prediction_readiness.py
"""
from __future__ import annotations

import gc
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import BigInteger, select
from sqlalchemy.engine import Engine
from sqlalchemy.event import listens_for
from sqlalchemy.ext.compiler import compiles

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    Category,
    DeliveryNote,
    DeliveryNoteItem,
    HistoricalDemandRecord,
    HistoricalImport,
    Product,
    StockMovement,
    User,
)
from app.models.historical_demand_record import (  # noqa: E402
    RECORD_STATUS_ISSUED,
    RECORD_TYPE_RETURN,
    RECORD_TYPE_SALE,
)
from app.models.historical_import import (  # noqa: E402
    IMPORT_STATUS_CONFIRMED,
    IMPORT_STATUS_REVERTED,
)
from app.models.user import ROLE_ADMIN, ROLE_INVENTARIO, ROLE_VENDEDOR  # noqa: E402

FAILURES = 0
CHECKS_RUN = 0
ADMIN_PASSWORD = "SecretPredPass999"
DISTINCTIVE_COST = "99999.13"
FORBIDDEN_KEYS = {
    "purchase_price",
    "raw_row_json",
    "storage_key",
    "password_hash",
    "unit_price",
}


@compiles(BigInteger, "sqlite")
def _compile_big_integer_for_sqlite(_type, _compiler, **_kwargs):
    return "INTEGER"


@listens_for(Engine, "connect")
def _configure_isolated_sqlite(dbapi_connection, _connection_record) -> None:
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def check(number: int | str, name: str, condition: bool, detail: str = "") -> None:
    global FAILURES, CHECKS_RUN
    CHECKS_RUN += 1
    ok = bool(condition)
    if not ok:
        FAILURES += 1
    suffix = f" — {detail}" if detail else ""
    print(f"[{'OK' if ok else 'FALLO'}] {number}. {name}{suffix}")


def close_num(actual: Any, expected: float, tol: float = 1e-6) -> bool:
    if actual is None:
        return False
    return abs(float(actual) - float(expected)) < tol


def contains_key(payload: Any, keys: set[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys:
                found.add(key)
            found |= contains_key(value, keys)
    elif isinstance(payload, list):
        for item in payload:
            found |= contains_key(item, keys)
    return found


def by_code(items: list[dict], code: str) -> dict:
    for item in items:
        if item.get("code") == code:
            return item
    raise KeyError(code)


def _canon(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def take_snapshot(app) -> dict[str, Any]:
    models = (
        ("products", Product),
        ("stock_movements", StockMovement),
        ("delivery_notes", DeliveryNote),
        ("delivery_note_items", DeliveryNoteItem),
        ("historical_imports", HistoricalImport),
        ("historical_demand_records", HistoricalDemandRecord),
        ("categories", Category),
        ("users", User),
    )
    tables: dict[str, dict[str, Any]] = {}
    with app.app_context():
        for name, model in models:
            rows = db.session.execute(select(model).order_by(model.id)).scalars().all()
            payload = []
            for row in rows:
                item = {}
                for column in model.__table__.columns:
                    item[column.name] = _canon(getattr(row, column.name))
                payload.append(item)
            digest = hashlib.sha256(
                json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
            tables[name] = {
                "count": len(rows),
                "ids": tuple(row.id for row in rows),
                "digest": digest,
            }
            if name == "products":
                tables[name]["stock"] = tuple(
                    (row.id, int(row.current_stock), bool(row.is_active)) for row in rows
                )
    return tables


def tables_equal(before: dict[str, Any], after: dict[str, Any], name: str) -> bool:
    return before.get(name) == after.get(name)


class IsolatedConfig:
    SECRET_KEY = secrets.token_urlsafe(32)
    APP_ENV = "testing"
    TESTING = False
    PROPAGATE_EXCEPTIONS = False
    WTF_CSRF_ENABLED = False
    MAIL_ENABLED = False
    GOOGLE_CLIENT_ID = None
    GOOGLE_CLIENT_SECRET = None
    GOOGLE_REDIRECT_URI = None
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"check_same_thread": False, "timeout": 30}
    }


def login(client, username: str, password: str):
    response = client.post(
        "/api/auth/login",
        json={"identifier": username, "password": password},
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"login {username} -> {response.status_code} {response.get_data(as_text=True)}"
        )
    return response


class HistoricalFactory:
    def __init__(self, hist_import: HistoricalImport):
        self.hist_import = hist_import
        self.row = 0

    def add(
        self,
        product: Product | None,
        event_date: date,
        quantity: int | Decimal,
        *,
        record_type: str = RECORD_TYPE_SALE,
        include_in_demand: bool = True,
        related_record_id: int | None = None,
        code_override: str | None = None,
    ) -> HistoricalDemandRecord:
        self.row += 1
        code = code_override or (product.code if product else "UNMATCHED")
        fingerprint = hashlib.sha256(
            f"{self.hist_import.id}:{self.row}:{code}:{event_date}:{quantity}:{record_type}".encode(
                "utf-8"
            )
        ).hexdigest()
        record = HistoricalDemandRecord(
            historical_import_id=self.hist_import.id,
            source_row_number=self.row,
            source_record_id_original=f"src-{self.row}",
            source_record_id_normalized=f"SRC-{self.row}",
            source_line_id_original=f"line-{self.row}",
            source_line_id_normalized=f"LINE-{self.row}",
            document_type="historical_demand",
            document_number_original=f"DOC-{self.row}",
            document_number_normalized=f"DOC-{self.row}",
            event_date=event_date,
            product_code_original=code,
            product_code_normalized=code.upper(),
            product_name_original=product.name if product else None,
            product_name_normalized=product.name.upper() if product else None,
            product_id=product.id if product else None,
            quantity=Decimal(str(quantity)),
            record_type=record_type,
            record_status=RECORD_STATUS_ISSUED,
            effective_status=RECORD_STATUS_ISSUED,
            related_record_id=related_record_id,
            fingerprint=fingerprint,
            fingerprint_strength="strong",
            dedupe_key=fingerprint,
            match_status="matched" if product else "unmatched",
            include_in_demand=include_in_demand,
            raw_row_json={
                "event_date": event_date.isoformat(),
                "product_code": code,
                "quantity": str(quantity),
                "record_type": record_type,
            },
        )
        db.session.add(record)
        db.session.flush()
        return record


def _make_import(user_id: int, status: str, tag: str) -> HistoricalImport:
    hist_import = HistoricalImport(
        original_filename=f"{tag}.csv",
        storage_key=hashlib.sha256(f"storage-{tag}".encode("utf-8")).hexdigest(),
        file_size_bytes=128,
        sha256=hashlib.sha256(f"file-{tag}".encode("utf-8")).hexdigest(),
        source_system="prediction-readiness-suite",
        status=status,
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        created_by_user_id=user_id,
        confirmed_by_user_id=user_id if status == IMPORT_STATUS_CONFIRMED else None,
        confirmed_at=datetime.utcnow() if status == IMPORT_STATUS_CONFIRMED else None,
        reverted_by_user_id=user_id if status == IMPORT_STATUS_REVERTED else None,
        reverted_at=datetime.utcnow() if status == IMPORT_STATUS_REVERTED else None,
        reversal_reason="lote de prueba revertido" if status == IMPORT_STATUS_REVERTED else None,
    )
    db.session.add(hist_import)
    db.session.flush()
    return hist_import


def _add_product(category_id: int, code: str, name: str, *, active: bool = True) -> Product:
    product = Product(
        code=code,
        name=name,
        category_id=category_id,
        unit="unidad",
        current_stock=100,
        minimum_stock=0,
        purchase_price=Decimal(DISTINCTIVE_COST),
        sale_price=Decimal("12.50"),
        is_active=active,
    )
    db.session.add(product)
    db.session.flush()
    return product


def _sales(factory: HistoricalFactory, product: Product, days: list[date], qty: int = 1) -> None:
    for day in days:
        factory.add(product, day, qty)


def setup_isolated_app(temp_root: Path) -> dict[str, Any]:
    db_path = temp_root / "prediction_readiness.sqlite"
    instance_path = temp_root / "instance"
    instance_path.mkdir(parents=True, exist_ok=True)
    IsolatedConfig.SQLALCHEMY_DATABASE_URI = (
        "sqlite:///" + db_path.resolve().as_posix() + "?check_same_thread=false"
    )
    IsolatedConfig.SECRET_KEY = secrets.token_urlsafe(32)
    app = create_app(IsolatedConfig)
    app.instance_path = str(instance_path.resolve())

    credentials = {
        "admin": ("pred_admin", ADMIN_PASSWORD),
        "inventario": ("pred_inventario", "inventario123"),
        "vendedor": ("pred_vendedor", "vendedor123"),
    }

    with app.app_context():
        db.create_all()
        category = Category(name="Categoría diagnóstico", description="Fixture temporal")
        db.session.add(category)
        db.session.flush()

        catalog = {
            "PRED-NONE": _add_product(category.id, "PRED-NONE", "Sin historial"),
            "PRED-ONE": _add_product(category.id, "PRED-ONE", "Un evento"),
            "PRED-TWO": _add_product(category.id, "PRED-TWO", "Dos eventos"),
            "PRED-LIMITED": _add_product(category.id, "PRED-LIMITED", "Historial limitado"),
            "PRED-SIMPLE": _add_product(category.id, "PRED-SIMPLE", "Apto simple"),
            "PRED-ADV": _add_product(category.id, "PRED-ADV", "Apto avanzado"),
            "PRED-INACT": _add_product(
                category.id, "PRED-INACT", "Inactivo con historial", active=False
            ),
            "PRED-STATS": _add_product(category.id, "PRED-STATS", "Serie de estadísticas"),
            "PRED-SAMEDAY": _add_product(category.id, "PRED-SAMEDAY", "Ventas mismo día"),
            "PRED-OPS": _add_product(category.id, "PRED-OPS", "Nota emitida"),
            "PRED-CANCEL": _add_product(category.id, "PRED-CANCEL", "Nota cancelada"),
            "PRED-MOVE": _add_product(category.id, "PRED-MOVE", "Solo movimientos"),
            "PRED-REVERT": _add_product(category.id, "PRED-REVERT", "Lote revertido"),
            "PRED-EXCL": _add_product(category.id, "PRED-EXCL", "Fuera de demanda"),
            "PRED-UNREL": _add_product(category.id, "PRED-UNREL", "Producto no relacionado"),
        }

        users_spec = [
            ("Administrador diagnóstico", "admin@pred.test", "admin", ROLE_ADMIN),
            ("Inventario diagnóstico", "inventario@pred.test", "inventario", ROLE_INVENTARIO),
            ("Vendedor diagnóstico", "vendedor@pred.test", "vendedor", ROLE_VENDEDOR),
        ]
        user_ids: dict[str, int] = {}
        for name, email, key, role in users_spec:
            username, password = credentials[key]
            user = User(
                name=name,
                email=email,
                username=username,
                role=role,
                is_active=True,
                email_verified=True,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            user_ids[key] = user.id

        confirmed = _make_import(user_ids["admin"], IMPORT_STATUS_CONFIRMED, "confirmed-2025")
        reverted = _make_import(user_ids["admin"], IMPORT_STATUS_REVERTED, "reverted-2025")
        factory = HistoricalFactory(confirmed)
        reverted_factory = HistoricalFactory(reverted)

        factory.add(catalog["PRED-ONE"], date(2025, 7, 1), 4)
        factory.add(catalog["PRED-TWO"], date(2025, 7, 10), 1)
        factory.add(catalog["PRED-TWO"], date(2025, 7, 12), 2)
        _sales(
            factory,
            catalog["PRED-LIMITED"],
            [date(2025, 6, 1), date(2025, 6, 3), date(2025, 6, 5), date(2025, 6, 8)],
        )
        _sales(
            factory,
            catalog["PRED-SIMPLE"],
            [
                date(2025, 4, 1),
                date(2025, 4, 5),
                date(2025, 4, 9),
                date(2025, 4, 13),
                date(2025, 4, 17),
                date(2025, 4, 21),
                date(2025, 4, 25),
                date(2025, 4, 30),
            ],
        )
        _sales(
            factory,
            catalog["PRED-ADV"],
            [
                date(2025, 1, 1),
                date(2025, 1, 6),
                date(2025, 1, 11),
                date(2025, 1, 16),
                date(2025, 1, 21),
                date(2025, 1, 26),
                date(2025, 1, 31),
                date(2025, 2, 5),
                date(2025, 2, 10),
                date(2025, 2, 15),
                date(2025, 2, 20),
                date(2025, 3, 1),
            ],
        )
        _sales(
            factory,
            catalog["PRED-INACT"],
            [
                date(2025, 5, 1),
                date(2025, 5, 5),
                date(2025, 5, 9),
                date(2025, 5, 13),
                date(2025, 5, 17),
                date(2025, 5, 21),
                date(2025, 5, 25),
                date(2025, 5, 30),
            ],
        )
        factory.add(catalog["PRED-STATS"], date(2025, 1, 1), 3)
        factory.add(catalog["PRED-STATS"], date(2025, 1, 3), 7)
        factory.add(catalog["PRED-SAMEDAY"], date(2025, 8, 1), 2)
        factory.add(catalog["PRED-SAMEDAY"], date(2025, 8, 1), 5)
        factory.add(catalog["PRED-UNREL"], date(2025, 9, 1), 9)
        factory.add(catalog["PRED-EXCL"], date(2025, 10, 1), 8, include_in_demand=False)
        reverted_factory.add(catalog["PRED-REVERT"], date(2025, 11, 1), 6)

        db.session.commit()
        ids = {code: product.id for code, product in catalog.items()}

    admin = app.test_client()
    inventario = app.test_client()
    vendedor = app.test_client()
    login(admin, credentials["admin"][0], credentials["admin"][1])
    login(inventario, credentials["inventario"][0], credentials["inventario"][1])
    login(vendedor, credentials["vendedor"][0], credentials["vendedor"][1])

    issued = admin.post(
        "/api/delivery-notes",
        json={
            "customer_name": "Cliente nota emitida",
            "items": [{"product_id": ids["PRED-OPS"], "quantity": 4}],
        },
    )
    if issued.status_code != 201:
        raise RuntimeError(f"No se pudo crear nota emitida: {issued.get_data(as_text=True)}")
    issued_note = issued.get_json()["delivery_note"]

    cancelled = admin.post(
        "/api/delivery-notes",
        json={
            "customer_name": "Cliente nota cancelada",
            "items": [{"product_id": ids["PRED-CANCEL"], "quantity": 3}],
        },
    )
    if cancelled.status_code != 201:
        raise RuntimeError(f"No se pudo crear nota a cancelar: {cancelled.get_data(as_text=True)}")
    cancelled_note = cancelled.get_json()["delivery_note"]
    cancel_resp = inventario.post(
        f"/api/delivery-notes/{cancelled_note['id']}/cancel", json={"reason": "Cancelación de prueba"}
    )
    if cancel_resp.status_code != 200:
        raise RuntimeError(f"No se pudo cancelar nota: {cancel_resp.get_data(as_text=True)}")

    entry = admin.post(
        "/api/inventory/entry",
        json={"product_id": ids["PRED-MOVE"], "quantity": 5, "reason": "entrada diagnóstico"},
    )
    if entry.status_code != 201:
        raise RuntimeError(f"No se pudo crear entrada: {entry.get_data(as_text=True)}")
    exit_move = admin.post(
        "/api/inventory/exit",
        json={"product_id": ids["PRED-MOVE"], "quantity": 2, "reason": "salida diagnóstico"},
    )
    if exit_move.status_code != 201:
        raise RuntimeError(f"No se pudo crear salida: {exit_move.get_data(as_text=True)}")
    adjustment = admin.post(
        "/api/inventory/adjustment",
        json={
            "product_id": ids["PRED-MOVE"],
            "new_stock": 80,
            "reason": "ajuste diagnóstico",
        },
    )
    if adjustment.status_code != 200 and adjustment.status_code != 201:
        raise RuntimeError(f"No se pudo crear ajuste: {adjustment.get_data(as_text=True)}")

    return {
        "app": app,
        "db_path": db_path,
        "admin": admin,
        "inventario": inventario,
        "vendedor": vendedor,
        "ids": ids,
        "issued": issued_note,
        "credentials": credentials,
    }


def dispose_app(app) -> None:
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    gc.collect()


def diagnose_real_db() -> dict[str, Any]:
    """Consulta de solo lectura a MySQL real, si está disponible."""
    try:
        import pymysql
        from dotenv import dotenv_values
    except Exception as error:
        return {"available": False, "reason": f"Dependencia ausente: {error}"}

    env = dotenv_values(ROOT / ".env") if (ROOT / ".env").is_file() else {}
    host = env.get("DB_HOST") or os.getenv("DB_HOST", "localhost")
    port = int(env.get("DB_PORT") or os.getenv("DB_PORT", "3306"))
    user = env.get("DB_USER") or os.getenv("DB_USER", "root")
    password = env.get("DB_PASSWORD") or os.getenv("DB_PASSWORD", "")
    name = env.get("DB_NAME") or os.getenv("DB_NAME", "ferreteria_conejo")
    try:
        connection = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=name,
            charset="utf8mb4",
            connect_timeout=4,
            read_timeout=8,
            autocommit=False,
        )
    except Exception as error:
        return {
            "available": False,
            "reason": f"No se pudo abrir MySQL de solo lectura: {error}",
        }

    result: dict[str, Any] = {"available": True, "database": name}
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM historical_imports WHERE status=%s",
                ("confirmed",),
            )
            result["confirmed_imports"] = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM historical_demand_records r "
                "INNER JOIN historical_imports i ON i.id = r.import_id "
                "WHERE i.status=%s AND r.include_in_demand=1 "
                "AND r.product_id IS NOT NULL "
                "AND r.effective_status IN ('issued', 'active')",
                ("confirmed",),
            )
            result["confirmed_demand_records"] = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM products")
            result["products"] = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM products WHERE is_active=1")
            result["active_products"] = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM delivery_notes WHERE status=%s", ("issued",)
            )
            result["issued_notes"] = int(cursor.fetchone()[0])
        connection.rollback()
    except Exception as error:
        connection.rollback()
        return {"available": False, "reason": f"Consulta de solo lectura falló: {error}"}
    finally:
        connection.close()
    return result


def print_real_db_report(info: dict[str, Any]) -> None:
    print("\n=== Estado actual con la BD real (solo lectura) ===")
    if not info.get("available"):
        print(
            "No se pudo consultar MySQL. Con historial confirmado vacío la UI "
            "mostraría «Sin historial» / «Historial insuficiente» y el mensaje "
            "«No existen datos históricos suficientes para generar una predicción confiable.»"
        )
        print(f"Detalle: {info.get('reason')}")
        return
    print(f"Base: {info.get('database')}")
    print(f"Lotes históricos confirmados: {info.get('confirmed_imports')}")
    print(f"Registros de demanda confirmados vigentes: {info.get('confirmed_demand_records')}")
    print(f"Productos: {info.get('products')} (activos: {info.get('active_products')})")
    print(f"Notas emitidas: {info.get('issued_notes')}")
    if not info.get("confirmed_imports"):
        print(
            "No hay importación 2025 confirmada. El diagnóstico operativo puede "
            "existir, pero la mayoría de productos clasificarán NO_HISTORY o "
            "INSUFFICIENT. Eso es un resultado válido, no un error."
        )


def main() -> int:
    print("=== Diagnóstico de suficiencia predictiva (suite aislada) ===\n")
    temp_root = Path(tempfile.mkdtemp(prefix="prediction_readiness_"))
    suite = None
    try:
        suite = setup_isolated_app(temp_root)
        app = suite["app"]
        admin = suite["admin"]
        inventario = suite["inventario"]
        vendedor = suite["vendedor"]
        ids = suite["ids"]
        issued = suite["issued"]

        before = take_snapshot(app)

        anon = app.test_client()
        r_page = anon.get("/predictions", follow_redirects=False)
        check(
            1,
            "/predictions sin sesión redirige",
            r_page.status_code == 302 and "login" in (r_page.headers.get("Location") or ""),
            f"status={r_page.status_code}",
        )

        r_admin_page = admin.get("/predictions")
        check(
            2,
            "Admin puede acceder",
            r_admin_page.status_code == 200
            and "Análisis predictivo y preparación de datos".encode("utf-8")
            in r_admin_page.data
            and "Análisis predictivo".encode("utf-8") in r_admin_page.data,
            f"status={r_admin_page.status_code}",
        )

        r_inv_page = inventario.get("/predictions")
        check(
            3,
            "Inventario puede acceder",
            r_inv_page.status_code == 200
            and "Análisis predictivo y preparación de datos".encode("utf-8") in r_inv_page.data,
        )

        r_seller_page = vendedor.get("/predictions", follow_redirects=False)
        check(
            4,
            "Vendedor no puede acceder",
            r_seller_page.status_code == 403
            and b"No tiene permisos para acceder a esta secci" in r_seller_page.data,
            f"status={r_seller_page.status_code}",
        )

        r401 = anon.get("/api/predictions/readiness")
        check(
            5,
            "API sin sesión 401 JSON",
            r401.status_code == 401 and r401.is_json and "error" in (r401.get_json() or {}),
            f"status={r401.status_code}",
        )

        r403 = vendedor.get("/api/predictions/readiness")
        check(
            6,
            "Vendedor API 403",
            r403.status_code == 403 and r403.is_json,
            f"status={r403.status_code}",
        )

        products_resp = admin.get("/api/predictions/products")
        products_json = products_resp.get_json() or {}
        items = products_json.get("items") or []
        check(
            "list",
            "GET /api/predictions/products responde",
            products_resp.status_code == 200 and isinstance(items, list) and items,
            f"status={products_resp.status_code} count={len(items)}",
        )

        none_item = by_code(items, "PRED-NONE")
        check(
            7,
            "Producto sin historial → NO_HISTORY",
            none_item["sufficiency_class"] == "NO_HISTORY"
            and none_item["positive_periods"] == 0,
            str(none_item.get("sufficiency_class")),
        )

        one_item = by_code(items, "PRED-ONE")
        check(
            8,
            "1 evento → INSUFFICIENT",
            one_item["sufficiency_class"] == "INSUFFICIENT"
            and one_item["original_event_count"] == 1
            and one_item["positive_periods"] == 1,
            str(one_item.get("sufficiency_class")),
        )

        two_item = by_code(items, "PRED-TWO")
        check(
            9,
            "2 eventos → INSUFFICIENT",
            two_item["sufficiency_class"] == "INSUFFICIENT"
            and two_item["original_event_count"] == 2
            and two_item["positive_periods"] == 2,
            str(two_item.get("sufficiency_class")),
        )

        limited_item = by_code(items, "PRED-LIMITED")
        check(
            10,
            "4 positivos con cobertura válida → LIMITED",
            limited_item["sufficiency_class"] == "LIMITED"
            and limited_item["positive_periods"] == 4
            and limited_item["periods"] >= 8,
            f"class={limited_item.get('sufficiency_class')} periods={limited_item.get('periods')}",
        )

        simple_item = by_code(items, "PRED-SIMPLE")
        check(
            11,
            "8 positivos y >=30 períodos → SIMPLE_READY",
            simple_item["sufficiency_class"] == "SIMPLE_READY"
            and simple_item["positive_periods"] == 8
            and simple_item["periods"] >= 30,
            f"class={simple_item.get('sufficiency_class')} periods={simple_item.get('periods')}",
        )

        adv_item = by_code(items, "PRED-ADV")
        check(
            12,
            ">=12 positivos y >=60 períodos → ADVANCED_READY",
            adv_item["sufficiency_class"] == "ADVANCED_READY"
            and adv_item["positive_periods"] >= 12
            and adv_item["periods"] >= 60,
            f"class={adv_item.get('sufficiency_class')} periods={adv_item.get('periods')}",
        )

        inactive_item = by_code(items, "PRED-INACT")
        check(
            13,
            "Producto inactivo no es candidato de reabastecimiento",
            inactive_item["is_active"] is False
            and inactive_item["readiness_for_replenishment"] is False
            and inactive_item["sufficiency_class"] == "SIMPLE_READY",
            str(inactive_item.get("readiness_for_replenishment")),
        )

        stats_detail = admin.get(f"/api/predictions/products/{ids['PRED-STATS']}").get_json()
        series = stats_detail.get("daily_series") or []
        series_map = {point["date"]: float(point["demand"]) for point in series}
        check(
            14,
            "Días sin demanda rellenados con cero",
            series_map.get("2025-01-01") == 3
            and series_map.get("2025-01-02") == 0
            and series_map.get("2025-01-03") == 7
            and len(series) == 3,
            str(series_map),
        )

        same_detail = admin.get(f"/api/predictions/products/{ids['PRED-SAMEDAY']}").get_json()
        check(
            15,
            "Varias ventas del mismo día se agregan",
            close_num(same_detail.get("total_demand"), 7)
            and same_detail.get("original_event_count") == 2
            and same_detail.get("periods") == 1
            and same_detail.get("positive_periods") == 1,
            f"total={same_detail.get('total_demand')} events={same_detail.get('original_event_count')}",
        )

        cancel_item = by_code(items, "PRED-CANCEL")
        check(
            16,
            "Nota cancelada excluida",
            cancel_item["sufficiency_class"] == "NO_HISTORY"
            and cancel_item["original_event_count"] == 0
            and close_num(cancel_item.get("total_demand") or 0, 0),
            str(cancel_item.get("sufficiency_class")),
        )

        ops_item = by_code(items, "PRED-OPS")
        check(
            17,
            "Nota emitida incluida",
            ops_item["positive_periods"] >= 1
            and close_num(ops_item.get("total_demand"), 4)
            and ops_item["data_source"] == "operational",
            f"total={ops_item.get('total_demand')} source={ops_item.get('data_source')}",
        )

        move_item = by_code(items, "PRED-MOVE")
        check(
            18,
            "stock_movements no se cuenta como demanda",
            move_item["sufficiency_class"] == "NO_HISTORY"
            and move_item["original_event_count"] == 0,
            str(move_item.get("sufficiency_class")),
        )
        check(
            19,
            "Entrada no se cuenta",
            move_item["original_event_count"] == 0 and close_num(move_item.get("total_demand") or 0, 0),
        )
        check(
            20,
            "Ajuste no se cuenta",
            move_item["sufficiency_class"] == "NO_HISTORY",
        )

        ops_detail = admin.get(f"/api/predictions/products/{ids['PRED-OPS']}").get_json()
        check(
            21,
            "Sin doble conteo nota+movimiento",
            close_num(ops_detail.get("total_demand"), 4)
            and ops_detail.get("original_event_count") == 1,
            f"total={ops_detail.get('total_demand')} events={ops_detail.get('original_event_count')}",
        )

        revert_item = by_code(items, "PRED-REVERT")
        check(
            22,
            "Lote histórico revertido excluido",
            revert_item["sufficiency_class"] == "NO_HISTORY"
            and revert_item["original_event_count"] == 0,
        )

        excl_item = by_code(items, "PRED-EXCL")
        check(
            23,
            "include_in_demand=false excluido",
            excl_item["sufficiency_class"] == "NO_HISTORY"
            and excl_item["original_event_count"] == 0,
        )

        unrel_item = by_code(items, "PRED-UNREL")
        check(
            24,
            "Producto no relacionado excluido del diagnóstico ajeno",
            none_item["original_event_count"] == 0
            and close_num(unrel_item.get("total_demand"), 9)
            and none_item["product_id"] != unrel_item["product_id"],
        )

        values = [3.0, 0.0, 7.0]
        expected_avg = sum(values) / 3
        expected_median = 3.0
        expected_std = (
            sum((value - expected_avg) ** 2 for value in values) / 3
        ) ** 0.5
        expected_zero_ratio = 1 / 3
        check(
            25,
            "Demanda total correcta",
            close_num(stats_detail.get("total_demand"), 10),
            str(stats_detail.get("total_demand")),
        )
        check(
            26,
            "Promedio correcto",
            close_num(stats_detail.get("average_daily_demand"), expected_avg),
            str(stats_detail.get("average_daily_demand")),
        )
        check(
            27,
            "Mediana correcta",
            close_num(stats_detail.get("median"), expected_median),
            str(stats_detail.get("median")),
        )
        check(
            28,
            "Stddev correcto",
            close_num(stats_detail.get("standard_deviation"), expected_std),
            str(stats_detail.get("standard_deviation")),
        )
        check(
            29,
            "zero_ratio correcto",
            close_num(stats_detail.get("zero_ratio"), expected_zero_ratio),
            str(stats_detail.get("zero_ratio")),
        )
        check(
            30,
            "Intervalo medio entre demandas correcto",
            close_num(stats_detail.get("average_interval_between_positive_demand"), 2.0),
            str(stats_detail.get("average_interval_between_positive_demand")),
        )

        readiness = admin.get("/api/predictions/readiness").get_json() or {}
        detail_keys = contains_key(readiness, FORBIDDEN_KEYS) | contains_key(
            products_json, FORBIDDEN_KEYS
        ) | contains_key(stats_detail, FORBIDDEN_KEYS) | contains_key(
            ops_detail, FORBIDDEN_KEYS
        )
        dumped = json.dumps(
            [readiness, products_json, stats_detail, ops_detail], ensure_ascii=False
        )
        check(
            31,
            "API no devuelve purchase_price",
            "purchase_price" not in detail_keys and DISTINCTIVE_COST.split(".")[0] not in dumped,
            str(detail_keys),
        )
        check(
            32,
            "API no devuelve raw_row_json",
            "raw_row_json" not in detail_keys and "raw_row_json" not in dumped,
        )

        r_dash = admin.get("/dashboard")
        r_dash_api = admin.get("/api/reports/dashboard-summary")
        check(
            38,
            "Dashboard sigue funcionando",
            r_dash.status_code == 200 and r_dash_api.status_code == 200,
            f"page={r_dash.status_code} api={r_dash_api.status_code}",
        )
        r_products = admin.get("/products")
        r_products_api = admin.get("/api/products")
        check(
            39,
            "Productos sigue funcionando",
            r_products.status_code == 200 and r_products_api.status_code == 200,
        )
        r_inv = admin.get("/inventory")
        r_inv_api = admin.get("/api/inventory/movements")
        check(
            40,
            "Inventario sigue funcionando",
            r_inv.status_code == 200 and r_inv_api.status_code == 200,
        )
        r_notes = admin.get("/delivery-notes")
        r_notes_api = admin.get("/api/delivery-notes")
        check(
            41,
            "Notas sigue funcionando",
            r_notes.status_code == 200 and r_notes_api.status_code == 200,
        )
        r_pdf = admin.get(f"/api/delivery-notes/{issued['id']}/pdf")
        check(
            42,
            "PDF sigue funcionando",
            r_pdf.status_code == 200
            and "application/pdf" in (r_pdf.headers.get("Content-Type") or ""),
            f"status={r_pdf.status_code} type={r_pdf.headers.get('Content-Type')}",
        )
        r_catalog = admin.get("/catalog")
        check(43, "Catálogo sigue funcionando", r_catalog.status_code == 200)
        r_chatbot = admin.get("/chatbot")
        r_chatbot_api = admin.post(
            "/api/chatbot/message", json={"message": "productos"}
        )
        check(
            44,
            "Chatbot sigue funcionando",
            r_chatbot.status_code == 200 and r_chatbot_api.status_code == 200,
            f"page={r_chatbot.status_code} api={r_chatbot_api.status_code}",
        )
        r_hist = admin.get("/historical-imports")
        r_hist_api = admin.get("/api/historical-imports")
        check(
            45,
            "Importación histórica sigue funcionando",
            r_hist.status_code == 200 and r_hist_api.status_code == 200,
        )

        again = admin.get("/api/predictions/products").get_json()
        check(
            46,
            "Resultados deterministas con los mismos datos",
            json.dumps(products_json, sort_keys=True) == json.dumps(again, sort_keys=True)
            and products_json.get("forecast_available") is False
            and readiness.get("forecast_available") is False
            and readiness.get("replenishment_available") is False
        )

        missing = admin.get("/api/predictions/products/999999")
        post_run = admin.post("/api/predictions/run", json={})
        post_readiness = admin.post("/api/predictions/readiness", json={})
        check(
            "api-404",
            "Producto inexistente → 404",
            missing.status_code == 404 and missing.is_json,
        )
        check(
            "no-run",
            "No existe POST /api/predictions/run",
            post_run.status_code == 404,
            f"status={post_run.status_code}",
        )
        check(
            "read-only",
            "POST /readiness no permitido",
            post_readiness.status_code == 405,
            f"status={post_readiness.status_code}",
        )
        seller_products = vendedor.get("/products")
        check(
            "nav",
            "Vendedor no ve Análisis predictivo en el menú",
            seller_products.status_code == 200
            and "Análisis predictivo".encode("utf-8") not in seller_products.data,
        )

        after = take_snapshot(app)
        check(33, "Stock no modificado", tables_equal(before, after, "products"))
        check(34, "No se crearon movimientos", tables_equal(before, after, "stock_movements"))
        check(35, "No se crearon notas", tables_equal(before, after, "delivery_notes"))
        check(
            36,
            "Históricos no modificados",
            tables_equal(before, after, "historical_demand_records")
            and tables_equal(before, after, "historical_imports"),
        )
        check(37, "Productos no modificados", tables_equal(before, after, "products"))

        print("\nSnapshot aislado (conteos):")
        for name in (
            "products",
            "stock_movements",
            "delivery_notes",
            "historical_demand_records",
        ):
            print(
                f"  {name}: antes={before[name]['count']} después={after[name]['count']} "
                f"igual={tables_equal(before, after, name)}"
            )

    except Exception as exc:
        import traceback

        traceback.print_exc()
        check("SETUP", "La suite aislada pudo inicializarse", False, str(exc))
        print(f"ERROR DE SUITE: {exc}")
    finally:
        if suite is not None:
            dispose_app(suite["app"])
        shutil.rmtree(temp_root, ignore_errors=True)
        leftover = temp_root.exists()
        leftover_sqlite = list(ROOT.rglob("prediction_readiness.sqlite"))
        check(
            47,
            "No quedan datos TEST",
            not leftover and not leftover_sqlite,
            f"temp={temp_root} sqlite={leftover_sqlite}",
        )
        check(
            48,
            "No quedan archivos temporales",
            not leftover,
            str(temp_root),
        )

    print_real_db_report(diagnose_real_db())

    print()
    print(f"Pruebas ejecutadas: {CHECKS_RUN}")
    if FAILURES == 0:
        print("RESULTADO: TODAS LAS PRUEBAS PASARON")
    else:
        print(f"RESULTADO: {FAILURES} PRUEBA(S) FALLARON")
    return FAILURES


if __name__ == "__main__":
    sys.exit(main())
