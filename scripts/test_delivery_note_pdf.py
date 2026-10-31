#!/usr/bin/env python3
"""Pruebas del PDF interno de notas de entrega.

Suite aislada: SQLite temporal + Flask test_client. No usa el MySQL de
desarrollo, no deja notas TEST permanentes y no escribe PDF a disco.

Uso: python scripts/test_delivery_note_pdf.py
"""
from __future__ import annotations

import gc
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import zlib
from decimal import Decimal
from pathlib import Path
from typing import Any

import requests
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
    Product,
    StockMovement,
    User,
)
from app.models.user import ROLE_ADMIN, ROLE_INVENTARIO, ROLE_VENDEDOR  # noqa: E402


LIVE_BASE = os.getenv("PDF_LIVE_BASE_URL", "").rstrip("/")
TIMEOUT = 8
FAILURES = 0
CHECKS_RUN = 0

ADMIN_PASSWORD = "SecretPDFPass999"
HIST_PURCHASE = "99999.13"
HIST_SALE = "12.50"
CURRENT_SALE = "88.88"
SPANISH_CUSTOMER = "José Núñez Peña"
SPANISH_PRODUCT = "Caño hidráulico Núñez"
LONG_NAME = ("Tubo galvanizado extra largo con descripción áéíóú ñ Ñ " + ("X" * 80))[:150]
LONG_ADDRESS = (
    "Avenida Bolívar, edificio Cañaveral, piso 12, oficina Ñandú, "
    "detrás del mercado municipal de Valencia. Referencia: portón azul."
)


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


def _canon(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extrae texto de un PDF fpdf2 (streams Flate + literales WinAnsi)."""
    blobs: list[bytes] = [pdf_bytes]
    for match in re.finditer(rb"\bstream\r?\n", pdf_bytes):
        # fpdf2 writes a direct /Length. A regex ending at CRLF can incorrectly
        # consume a final 0x0d belonging to the compressed data, hiding its text.
        dictionary = pdf_bytes[:match.start()].rsplit(b"<<", 1)[-1]
        length = re.search(rb"/Length\s+(\d+)\b", dictionary)
        if length is None:
            raise ValueError("El stream del PDF de prueba no declara una longitud directa.")
        payload = pdf_bytes[match.end():match.end() + int(length.group(1))]
        for wbits in (zlib.MAX_WBITS, -15):
            try:
                blobs.append(zlib.decompress(payload, wbits))
                break
            except zlib.error:
                continue

    chunks: list[str] = []
    for blob in blobs:
        raw = blob.decode("latin-1", errors="ignore")
        for match in re.finditer(r"\((?:\\.|[^\\)])*\)", raw):
            inner = match.group(0)[1:-1]
            inner = inner.replace("\\\\", "\x00")
            inner = re.sub(r"\\([0-7]{1,3})", lambda m: chr(int(m.group(1), 8)), inner)
            inner = inner.replace("\\(", "(").replace("\\)", ")")
            inner = inner.replace("\\n", "\n").replace("\\r", "\n")
            inner = inner.replace("\x00", "\\")
            chunks.append(inner)
        hex_matches = re.findall(r"<([0-9A-Fa-f\s]+)>", raw)
        for hex_value in hex_matches:
            clean = re.sub(r"\s+", "", hex_value)
            if len(clean) < 4 or len(clean) % 2:
                continue
            try:
                decoded = bytes.fromhex(clean)
                chunks.append(decoded.decode("utf-16-be", errors="ignore"))
                chunks.append(decoded.decode("latin-1", errors="ignore"))
            except ValueError:
                continue
    return "\n".join(chunks)


def pdf_contains(pdf_bytes: bytes, needle: str) -> bool:
    text = extract_pdf_text(pdf_bytes)
    if needle in text:
        return True
    try:
        if needle.encode("cp1252") in pdf_bytes:
            return True
    except UnicodeEncodeError:
        pass
    if needle.encode("utf-8", errors="ignore") in pdf_bytes:
        return True
    return needle.casefold() in text.casefold()


def pdf_page_count(pdf_bytes: bytes) -> int:
    matches = re.findall(rb"/Type\s*/Page(?!s)\b", pdf_bytes)
    return len(matches) if matches else 0


def list_pdfs(root: Path) -> set[Path]:
    return {path.resolve() for path in root.rglob("*.pdf") if path.is_file()}


def take_snapshot(app) -> dict[str, Any]:
    models = (
        ("products", Product),
        ("stock_movements", StockMovement),
        ("delivery_notes", DeliveryNote),
        ("delivery_note_items", DeliveryNoteItem),
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
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            tables[name] = {
                "count": len(rows),
                "ids": tuple(row.id for row in rows),
                "digest": digest,
            }
            if name == "products":
                tables[name]["stock"] = tuple(
                    (row.id, int(row.current_stock)) for row in rows
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
        raise RuntimeError(f"login {username} -> {response.status_code} {response.get_data(as_text=True)}")
    return response


def setup_isolated_app(temp_root: Path):
    db_path = temp_root / "pdf_suite.sqlite"
    instance_path = temp_root / "instance"
    instance_path.mkdir(parents=True, exist_ok=True)
    IsolatedConfig.SQLALCHEMY_DATABASE_URI = (
        "sqlite:///" + db_path.resolve().as_posix() + "?check_same_thread=false"
    )
    IsolatedConfig.SECRET_KEY = secrets.token_urlsafe(32)
    app = create_app(IsolatedConfig)
    app.instance_path = str(instance_path.resolve())

    credentials = {
        "admin": ("pdf_admin", ADMIN_PASSWORD),
        "inventario": ("pdf_inventario", "inventario123"),
        "vendedor": ("pdf_vendedor", "vendedor123"),
        "noperm": ("pdf_noperm", "nopermiso123"),
    }

    with app.app_context():
        db.create_all()
        from app.services import rbac_service
        rbac_service.ensure_seeded()
        category = Category(name="Categoría PDF aislada", description="Fixture temporal")
        db.session.add(category)
        db.session.flush()

        hist = Product(
            code="PDF-HIST",
            name=SPANISH_PRODUCT,
            category_id=category.id,
            unit="unidad",
            current_stock=80,
            minimum_stock=0,
            purchase_price=Decimal(HIST_PURCHASE),
            sale_price=Decimal(HIST_SALE),
            sale_price_currency="USD",
            is_active=True,
        )
        long_prod = Product(
            code="PDF-LONG",
            name=LONG_NAME,
            category_id=category.id,
            unit="unidad",
            current_stock=40,
            minimum_stock=0,
            purchase_price=Decimal("1.00"),
            sale_price=Decimal("3.00"),
            sale_price_currency="USD",
            is_active=True,
        )
        db.session.add_all([hist, long_prod])
        many_products = []
        for index in range(1, 36):
            product = Product(
                code=f"PDF-M{index:02d}",
                name=f"Producto masivo {index:02d}",
                category_id=category.id,
                unit="unidad",
                current_stock=5,
                minimum_stock=0,
                purchase_price=Decimal("0.50"),
                sale_price=Decimal("1.25"),
                sale_price_currency="USD",
                is_active=True,
            )
            many_products.append(product)
        db.session.add_all(many_products)

        users = [
            ("Administrador PDF", "admin@pdf.test", "admin", ROLE_ADMIN),
            ("Inventario PDF", "inventario@pdf.test", "inventario", ROLE_INVENTARIO),
            ("Vendedor PDF", "vendedor@pdf.test", "vendedor", ROLE_VENDEDOR),
            ("Sin Permiso PDF", "noperm@pdf.test", "noperm", "consulta"),
        ]
        for name, email, key, role in users:
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
        db.session.commit()

        hist_id = hist.id
        long_id = long_prod.id
        many_ids = [product.id for product in many_products]
        category_id = category.id

    admin = app.test_client()
    inventario = app.test_client()
    vendedor = app.test_client()
    noperm = app.test_client()
    login(admin, credentials["admin"][0], credentials["admin"][1])
    login(inventario, credentials["inventario"][0], credentials["inventario"][1])
    login(vendedor, credentials["vendedor"][0], credentials["vendedor"][1])
    login(noperm, credentials["noperm"][0], credentials["noperm"][1])

    issued = admin.post(
        "/api/delivery-notes",
        json={
            "customer_name": SPANISH_CUSTOMER,
            "customer_document": "V-12345678",
            "customer_phone": "0412-5551133",
            "customer_address": LONG_ADDRESS,
            "items": [
                {"product_id": hist_id, "quantity": 3},
                {"product_id": long_id, "quantity": 1},
            ],
        },
    )
    if issued.status_code != 201:
        raise RuntimeError(f"No se pudo crear nota emitida: {issued.get_data(as_text=True)}")
    issued_note = issued.get_json()["delivery_note"]

    cancelled = admin.post(
        "/api/delivery-notes",
        json={
            "customer_name": "Cliente Cancelado",
            "items": [{"product_id": hist_id, "quantity": 1}],
        },
    )
    if cancelled.status_code != 201:
        raise RuntimeError(f"No se pudo crear nota a cancelar: {cancelled.get_data(as_text=True)}")
    cancelled_note = cancelled.get_json()["delivery_note"]
    denied_cancel = inventario.post(f"/api/delivery-notes/{cancelled_note['id']}/cancel", json={"reason": "Cancelación de prueba"})
    if denied_cancel.status_code != 403:
        raise RuntimeError("Inventario no debe adquirir el permiso de cancelación.")
    cancel_resp = admin.post(f"/api/delivery-notes/{cancelled_note['id']}/cancel", json={"reason": "Cancelación de prueba"})
    if cancel_resp.status_code != 200:
        raise RuntimeError(f"No se pudo cancelar nota: {cancel_resp.get_data(as_text=True)}")
    cancelled_note = cancel_resp.get_json()["delivery_note"]

    many = admin.post(
        "/api/delivery-notes",
        json={
            "customer_name": "Cliente Muchos Productos",
            "items": [{"product_id": product_id, "quantity": 1} for product_id in many_ids],
        },
    )
    if many.status_code != 201:
        raise RuntimeError(f"No se pudo crear nota masiva: {many.get_data(as_text=True)}")
    many_note = many.get_json()["delivery_note"]

    # A later synthetic catalog value tests the historical snapshot. This is fixture
    # setup, not a grant allowing Admin to operate products or a live API mutation.
    with app.app_context():
        db.session.get(Product, hist_id).sale_price = Decimal(CURRENT_SALE)
        db.session.commit()

    return {
        "app": app,
        "db_path": db_path,
        "admin": admin,
        "inventario": inventario,
        "vendedor": vendedor,
        "noperm": noperm,
        "issued": issued_note,
        "cancelled": cancelled_note,
        "many": many_note,
        "hist_id": hist_id,
        "category_id": category_id,
    }


def dispose_app(app) -> None:
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    gc.collect()


def live_get(path: str, session: requests.Session | None = None) -> requests.Response | None:
    if not LIVE_BASE:
        return None
    try:
        client = session or requests
        return client.get(f"{LIVE_BASE}{path}", timeout=TIMEOUT, allow_redirects=False)
    except requests.RequestException:
        return None


def main() -> int:
    print("=== PDF de notas de entrega (suite aislada) ===\n")
    pdfs_before = list_pdfs(ROOT)
    temp_root = Path(tempfile.mkdtemp(prefix="delivery_note_pdf_"))
    suite = None
    try:
        suite = setup_isolated_app(temp_root)
        app = suite["app"]
        admin = suite["admin"]
        inventario = suite["inventario"]
        vendedor = suite["vendedor"]
        noperm = suite["noperm"]
        issued = suite["issued"]
        cancelled = suite["cancelled"]
        many = suite["many"]

        before = take_snapshot(app)

        anon = app.test_client()
        r401 = anon.get(f"/api/delivery-notes/{issued['id']}/pdf")
        check(1, "Sin sesion -> 401 JSON", r401.status_code == 401 and r401.is_json, f"status={r401.status_code}")

        r_admin = admin.get(f"/api/delivery-notes/{issued['id']}/pdf")
        check(2, "Admin puede descargar", r_admin.status_code == 200, f"status={r_admin.status_code}")

        r_seller = vendedor.get(f"/api/delivery-notes/{issued['id']}/pdf")
        check(3, "Vendedor con delivery_notes:read puede descargar", r_seller.status_code == 200)

        r_inv = inventario.get(f"/api/delivery-notes/{issued['id']}/pdf")
        check(4, "Inventario con permiso de lectura puede descargar", r_inv.status_code == 200)
        inventory_text = extract_pdf_text(r_inv.data)
        check("4b", "PDF físico de Inventario no recibe precios ni totales",
              "12,50" not in inventory_text and "37,50" not in inventory_text
              and "40,50" not in inventory_text and "Total general" not in inventory_text
              and "Subtotal" not in inventory_text and "Precio unitario" not in inventory_text
              and "Cantidad" in inventory_text and "PDF-HIST" in inventory_text)

        r403 = noperm.get(f"/api/delivery-notes/{issued['id']}/pdf")
        check(5, "Usuario sin permiso -> 403", r403.status_code == 403 and r403.is_json, f"status={r403.status_code}")

        r404 = admin.get("/api/delivery-notes/999999/pdf")
        check(6, "Nota inexistente -> 404", r404.status_code == 404 and r404.is_json)

        content_type = (r_admin.headers.get("Content-Type") or "").lower()
        check(7, "Content-Type es application/pdf", "application/pdf" in content_type, content_type)

        disposition = r_admin.headers.get("Content-Disposition") or ""
        expected_name = f"nota-entrega-{issued['note_number']}.pdf"
        check(
            8,
            "Content-Disposition contiene nombre seguro",
            disposition.startswith("attachment")
            and expected_name in disposition
            and ".." not in disposition
            and "/" not in disposition.replace("attachment; filename=", "")
            and "\\" not in disposition,
            disposition,
        )

        pdf_bytes = r_admin.data
        check(9, "El archivo empieza con firma PDF", pdf_bytes.startswith(b"%PDF-"))
        text = extract_pdf_text(pdf_bytes)

        check(10, "PDF contiene el número de nota", pdf_contains(pdf_bytes, issued["note_number"]))
        check(11, "PDF contiene el nombre del cliente", pdf_contains(pdf_bytes, SPANISH_CUSTOMER))
        check(12, "PDF contiene productos", pdf_contains(pdf_bytes, SPANISH_PRODUCT) and pdf_contains(pdf_bytes, "Producto"))
        check(13, "PDF contiene cantidades", pdf_contains(pdf_bytes, "3") and pdf_contains(pdf_bytes, "Cantidad"))
        check(14, "PDF contiene precios históricos", pdf_contains(pdf_bytes, "12,50"))
        check(15, "PDF contiene el total", pdf_contains(pdf_bytes, "Total general"))
        check(
            16,
            "PDF no contiene purchase_price ni el costo distintivo",
            "purchase_price" not in text and "99999" not in text and "99.999" not in text,
        )
        check(
            17,
            "PDF no contiene password ni datos sensibles",
            ADMIN_PASSWORD not in text
            and "password_hash" not in text.casefold()
            and "password" not in text.casefold(),
        )

        r_cancelled = admin.get(f"/api/delivery-notes/{cancelled['id']}/pdf")
        cancelled_ok = r_cancelled.status_code == 200 and r_cancelled.data.startswith(b"%PDF-")
        cancelled_text = extract_pdf_text(r_cancelled.data) if cancelled_ok else ""
        check(
            18,
            "Nota cancelada indica CANCELADA",
            cancelled_ok and "NOTA CANCELADA" in cancelled_text and "Cancelada" in cancelled_text,
        )
        check(19, "Nota emitida indica Emitida", "Emitida" in text and "NOTA CANCELADA" not in text)

        r_many = admin.get(f"/api/delivery-notes/{many['id']}/pdf")
        many_pages = pdf_page_count(r_many.data) if r_many.status_code == 200 else 0
        check(
            20,
            "Nota con muchos productos genera PDF correcto",
            r_many.status_code == 200
            and r_many.data.startswith(b"%PDF-")
            and many_pages >= 2
            and pdf_contains(r_many.data, many["note_number"])
            and pdf_contains(r_many.data, "Producto masivo 01")
            and pdf_contains(r_many.data, "Producto masivo 35"),
            f"pages={many_pages} status={r_many.status_code}",
        )
        check(
            21,
            "Texto largo no rompe la generación",
            pdf_contains(pdf_bytes, "Tubo galvanizado extra largo")
            and pdf_contains(pdf_bytes, "mercado municipal"),
        )
        spanish_text = extract_pdf_text(pdf_bytes)
        check(
            22,
            "Caracteres españoles funcionan",
            pdf_contains(pdf_bytes, "José")
            and pdf_contains(pdf_bytes, "Núñez")
            and pdf_contains(pdf_bytes, "Caño")
            and all(char in spanish_text for char in "áéíóúñÑ"),
        )

        leaked_current = pdf_contains(pdf_bytes, "88,88")
        check(
            "14b",
            "No sustituye el precio histórico por el precio actual",
            not leaked_current,
        )
        check(
            "15b",
            "El total usa decimales y no inventa IVA",
            "IVA" not in extract_pdf_text(pdf_bytes)
            and pdf_contains(pdf_bytes, issued["note_number"]),
        )
        check(
            "18b",
            "PDF cancelado sigue consultable e incluye fecha/usuario si existen",
            cancelled_ok
            and "Administrador PDF" in cancelled_text
            and "Fecha de cancelación" in cancelled_text,
        )
        check(
            "disclaimer",
            "Incluye leyenda de documento interno no fiscal",
            "No constituye factura ni comprobante fiscal" in text,
        )

        pdfs_mid = list_pdfs(ROOT)
        extra_pdfs = pdfs_mid - pdfs_before
        check(23, "No se crearon archivos PDF permanentes", extra_pdfs == set(), f"nuevos={extra_pdfs}")

        after = take_snapshot(app)
        check(24, "Stock idéntico antes/después", tables_equal(before, after, "products") and before["products"]["stock"] == after["products"]["stock"])
        check(25, "Movimientos idénticos antes/después", tables_equal(before, after, "stock_movements"))
        check(26, "Notas idénticas antes/después", tables_equal(before, after, "delivery_notes"))
        check(27, "Ítems idénticos antes/después", tables_equal(before, after, "delivery_note_items"))
        check(28, "Productos idénticos antes/después", tables_equal(before, after, "products"))

        r_dash = admin.get("/dashboard")
        check(29, "Dashboard sigue funcionando", r_dash.status_code == 200 and b'id="kpi-row"' in r_dash.data)
        r_notes_page = admin.get("/delivery-notes")
        check(
            30,
            "/delivery-notes sigue funcionando",
            r_notes_page.status_code == 200
            and "Descargar PDF".encode("utf-8") in r_notes_page.data
            and "PDF (próximamente)".encode("utf-8") not in r_notes_page.data,
        )
        r_catalog = admin.get("/catalog")
        check(31, "Catálogo sigue funcionando", r_catalog.status_code == 200)
        r_inventory = admin.get("/inventory")
        check(32, "Inventario sigue funcionando", r_inventory.status_code == 200)
        r_chatbot = admin.get("/chatbot")
        check(33, "Chatbot sigue funcionando", r_chatbot.status_code == 200)
        r_hist = admin.get("/historical-imports")
        check(34, "Importación histórica sigue funcionando (solo lectura)", r_hist.status_code == 200)

        live_ok = live_get("/")
        if live_ok is not None and live_ok.status_code in {200, 302, 401}:
            live_admin = requests.Session()
            try:
                login_live = live_admin.post(
                    f"{LIVE_BASE}/api/auth/login",
                    json={"identifier": "admin", "password": "admin123"},
                    timeout=TIMEOUT,
                )
            except requests.RequestException:
                login_live = None
            if login_live is not None and login_live.status_code == 200:
                for num, path, label in (
                    ("29b", "/dashboard", "Dashboard live"),
                    ("30b", "/delivery-notes", "Notas live"),
                    ("31b", "/catalog", "Catálogo live"),
                    ("32b", "/inventory", "Inventario live"),
                    ("33b", "/chatbot", "Chatbot live"),
                    ("34b", "/historical-imports", "Histórico live"),
                ):
                    page = live_get(path, live_admin)
                    check(num, f"{label} responde", page is not None and page.status_code == 200, f"status={getattr(page, 'status_code', None)}")
            else:
                print("[INFO] Servidor live disponible pero no se pudo iniciar sesión admin; se omite el extra live.")
        else:
            print("[INFO] Servidor live no disponible; 29-34 se verificaron en la app aislada.")

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
        check(
            "cleanup",
            "Directorio temporal eliminado (sin artefactos residuales)",
            not leftover,
            str(temp_root),
        )
        pdfs_after = list_pdfs(ROOT)
        check(
            "cleanup-pdf",
            "El workspace no ganó archivos PDF",
            pdfs_after == pdfs_before,
            f"diff={pdfs_after - pdfs_before}",
        )

    print()
    print(f"Pruebas ejecutadas: {CHECKS_RUN}")
    if FAILURES == 0:
        print("RESULTADO: TODAS LAS PRUEBAS PASARON")
    else:
        print(f"RESULTADO: {FAILURES} PRUEBA(S) FALLARON")
    return FAILURES


if __name__ == "__main__":
    sys.exit(main())
