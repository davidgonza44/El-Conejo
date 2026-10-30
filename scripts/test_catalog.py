#!/usr/bin/env python3
"""Verificación de 29 puntos del catálogo visual, sin mutar datos operativos.

Combina peticiones HTTP de solo lectura con aserciones estáticas para las
interacciones que requieren navegador (debounce, filtros, orden y responsive).

Uso:
    python scripts/test_catalog.py

Variables opcionales:
    CATALOG_BASE_URL, CATALOG_ADMIN_USER, CATALOG_ADMIN_PASSWORD,
    CATALOG_INVENTORY_USER, CATALOG_INVENTORY_PASSWORD,
    CATALOG_SELLER_USER, CATALOG_SELLER_PASSWORD
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("CATALOG_BASE_URL", "http://localhost:5000").rstrip("/")
TIMEOUT = 10
EXPECTED_CHECKS = 29
FAILURES = 0
CHECKS_RUN = 0

PATHS = {
    "pages": ROOT / "app" / "routes" / "pages.py",
    "base": ROOT / "app" / "templates" / "base_app.html",
    "template": ROOT / "app" / "templates" / "catalog.html",
    "js": ROOT / "app" / "static" / "js" / "catalog.js",
    "css": ROOT / "app" / "static" / "css" / "catalog.css",
    "products_controller": ROOT / "app" / "controllers" / "product_controller.py",
    "product_model": ROOT / "app" / "models" / "product.py",
    "product_service": ROOT / "app" / "services" / "product_service.py",
}

ROLE_CREDENTIALS = {
    "admin": (
        os.getenv("CATALOG_ADMIN_USER", "admin"),
        os.getenv("CATALOG_ADMIN_PASSWORD", "admin123"),
    ),
    "inventario": (
        os.getenv("CATALOG_INVENTORY_USER", "inventario1"),
        os.getenv("CATALOG_INVENTORY_PASSWORD", "inventario123"),
    ),
    "vendedor": (
        os.getenv("CATALOG_SELLER_USER", "vendedor1"),
        os.getenv("CATALOG_SELLER_PASSWORD", "vendedor123"),
    ),
}


def read_sources() -> dict[str, str]:
    missing = [str(path) for path in PATHS.values() if not path.is_file()]
    if missing:
        raise RuntimeError(f"Faltan archivos requeridos: {', '.join(missing)}")
    return {name: path.read_text(encoding="utf-8") for name, path in PATHS.items()}


def check(number: int, name: str, condition: bool, detail: str = "") -> None:
    global FAILURES, CHECKS_RUN
    CHECKS_RUN += 1
    ok = bool(condition)
    if not ok:
        FAILURES += 1
    suffix = f" — {detail}" if detail else ""
    print(f"[{'OK' if ok else 'FALLO'}] {number:02d}. {name}{suffix}")


def get_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except requests.JSONDecodeError:
        return None


def login(identifier: str, password: str) -> tuple[requests.Session | None, str]:
    session = requests.Session()
    try:
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"identifier": identifier, "password": password},
            timeout=TIMEOUT,
        )
    except requests.RequestException as error:
        return None, str(error)
    if response.status_code == 200:
        return session, ""
    data = get_json(response)
    message = data.get("error", "") if isinstance(data, dict) else ""
    detail = f"HTTP {response.status_code}"
    if message:
        detail += f": {message}"
    return None, detail


def json_fingerprint(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def operational_snapshot(session: requests.Session) -> dict[str, Any] | None:
    endpoints = {
        "products": "/api/products",
        "movements": "/api/inventory/movements",
        "delivery_notes": "/api/delivery-notes",
    }
    snapshot: dict[str, Any] = {}
    for key, path in endpoints.items():
        try:
            response = session.get(f"{BASE_URL}{path}", timeout=TIMEOUT)
        except requests.RequestException:
            return None
        data = get_json(response)
        if response.status_code != 200 or not isinstance(data, dict):
            return None
        if key == "products":
            snapshot[key] = sorted(
                (
                    item.get("id"),
                    item.get("current_stock"),
                    item.get("is_active"),
                )
                for item in data.get("items", [])
            )
        else:
            snapshot[key] = {
                "count": data.get("count"),
                "ids": sorted(item.get("id") for item in data.get("items", [])),
            }
    return snapshot


def request_ok(session: requests.Session, path: str, text: str = "") -> bool:
    try:
        response = session.get(f"{BASE_URL}{path}", timeout=TIMEOUT)
    except requests.RequestException:
        return False
    return response.status_code == 200 and (not text or text in response.text)


def main() -> int:
    try:
        sources = read_sources()
    except RuntimeError as error:
        print(f"[SETUP-ERROR] {error}")
        return 2

    try:
        health = requests.get(f"{BASE_URL}/login", timeout=TIMEOUT)
    except requests.RequestException as error:
        print(f"[SETUP-ERROR] Servidor no disponible en {BASE_URL}: {error}")
        print("Inicie el servidor con: python run.py")
        return 2
    if health.status_code != 200:
        print(f"[SETUP-ERROR] GET /login respondió {health.status_code}")
        return 2

    login_results = {
        role: login(identifier, password)
        for role, (identifier, password) in ROLE_CREDENTIALS.items()
    }
    sessions = {role: result[0] for role, result in login_results.items()}
    unavailable_roles = [role for role, session in sessions.items() if session is None]
    if unavailable_roles:
        details = "; ".join(
            f"{role}: {login_results[role][1]}" for role in unavailable_roles
        )
        print(f"[SETUP-ERROR] No se pudo iniciar sesión ({details}).")
        if any("HTTP 500" in login_results[role][1] for role in unavailable_roles):
            print("Verifique que MySQL esté disponible y configurado.")
        else:
            print("Ajuste las credenciales con las variables CATALOG_*.")
        return 2

    admin = sessions["admin"]
    inventario = sessions["inventario"]
    vendedor = sessions["vendedor"]
    assert admin is not None and inventario is not None and vendedor is not None

    before = operational_snapshot(admin)

    unauthenticated = requests.get(
        f"{BASE_URL}/catalog", allow_redirects=False, timeout=TIMEOUT
    )
    check(
        1,
        "Sin sesión redirige a /login",
        unauthenticated.status_code == 302
        and "/login" in (unauthenticated.headers.get("Location") or ""),
    )

    check(2, "Admin accede a /catalog", request_ok(admin, "/catalog", "Catálogo visual"))
    check(
        3,
        "Inventario accede a /catalog",
        request_ok(inventario, "/catalog", "Catálogo visual"),
    )
    check(
        4,
        "Vendedor accede a /catalog",
        request_ok(vendedor, "/catalog", "Catálogo visual"),
    )

    admin_catalog = admin.get(f"{BASE_URL}/catalog", timeout=TIMEOUT)
    check(
        5,
        "Ruta, enlace sidebar y active_page",
        '@pages_bp.get("/catalog")' in sources["pages"]
        and "@login_required" in sources["pages"]
        and 'href="/catalog"' in sources["base"]
        and "Catálogo visual" in sources["base"]
        and 'active_page = "catalog"' in sources["template"]
        and 'nav-item active' in admin_catalog.text,
    )

    products_response = admin.get(f"{BASE_URL}/api/products", timeout=TIMEOUT)
    products_data = get_json(products_response)
    products = (
        products_data.get("items", [])
        if isinstance(products_data, dict) and isinstance(products_data.get("items"), list)
        else []
    )
    check(
        6,
        "API y grid trabajan solo con activos",
        products_response.status_code == 200
        and all(item.get("is_active") is True for item in products)
        and 'product.is_active === true' in sources["js"],
    )
    check(
        7,
        "Inactivos quedan excluidos explícitamente",
        "if not include_inactive:" in sources["product_service"]
        and "if (product.is_active !== true) return false;" in sources["js"],
    )

    check(
        8,
        "Renderiza image_url o placeholder",
        "product.image_url" in sources["js"]
        and "Imagen no disponible" in sources["js"]
        and "ti-photo-off" in sources["js"],
    )
    check(
        9,
        "Imagen rota activa fallback onerror",
        'addEventListener("error"' in sources["js"]
        and "renderImagePlaceholder(holder)" in sources["js"],
    )
    check(
        10,
        "Búsqueda por nombre con debounce",
        "SEARCH_DEBOUNCE_MS = 300" in sources["js"]
        and "product.name" in sources["js"]
        and 'addEventListener("input"' in sources["js"],
    )
    check(
        11,
        "Búsqueda por código",
        "product.code" in sources["js"]
        and "searchable.some((value) => value.includes(term))" in sources["js"],
    )
    check(
        12,
        "Búsqueda incluye descripción real",
        '"description"' in sources["js"]
        and '"description": self.description' in sources["product_model"],
    )
    check(
        13,
        "Filtro de categoría usa category_id",
        'id="catalog-category"' in sources["template"]
        and "product.category_id" in sources["js"]
        and 'apiGet("/api/categories")' in sources["js"],
    )
    check(
        14,
        "Filtro Disponible",
        'value="available">Disponible' in sources["template"]
        and 'key: "available"' in sources["js"],
    )
    check(
        15,
        "Filtro Bajo stock",
        'value="low">Bajo stock' in sources["template"]
        and "current <= minimum" in sources["js"],
    )
    check(
        16,
        "Filtro Sin existencia",
        'value="out">Sin existencia' in sources["template"]
        and "current <= 0" in sources["js"],
    )
    check(
        17,
        "Orden nombre A-Z y Z-A",
        '"name-asc"' in sources["js"]
        and '"name-desc"' in sources["js"]
        and 'localeCompare(String(b.name || ""), "es"' in sources["js"],
    )
    check(
        18,
        "Orden precio menor y mayor",
        '"price-asc"' in sources["js"]
        and '"price-desc"' in sources["js"]
        and "numericValue(a.sale_price)" in sources["js"],
    )
    check(
        19,
        "Orden stock menor y mayor",
        '"stock-asc"' in sources["js"]
        and '"stock-desc"' in sources["js"]
        and "numericValue(a.current_stock)" in sources["js"],
    )
    check(
        20,
        "Paginación cliente de 12 y filtros persistentes",
        "PAGE_SIZE = 12" in sources["js"]
        and 'id="catalog-prev"' in sources["template"]
        and 'id="catalog-next"' in sources["template"]
        and "window.history.replaceState" in sources["js"]
        and "restoreFiltersFromUrl" in sources["js"],
    )

    detail_ok = False
    if products:
        detail_response = admin.get(
            f"{BASE_URL}/api/products/{products[0].get('id')}", timeout=TIMEOUT
        )
        detail = get_json(detail_response)
        detail_ok = (
            detail_response.status_code == 200
            and isinstance(detail, dict)
            and {
                "id",
                "code",
                "name",
                "description",
                "category",
                "sale_price",
                "current_stock",
                "minimum_stock",
                "is_active",
            }.issubset(detail)
        )
    check(
        21,
        "Detalle GET y modal de solo consulta",
        detail_ok
        and 'id="catalog-detail-modal"' in sources["template"]
        and "apiGet(`/api/products/${encodeURIComponent(productId)}`)" in sources["js"]
        and ".innerHTML" not in sources["js"]
        and ".textContent" in sources["js"]
        and all(
            token not in sources["js"]
            for token in ['method: "POST"', 'method: "PUT"', 'method: "PATCH"', 'method: "DELETE"']
        ),
        "requiere al menos un producto activo" if not products else "",
    )
    check(
        22,
        'Estado vacío exacto "Sin productos encontrados"',
        'title.textContent = "Sin productos encontrados"' in sources["js"],
    )
    check(
        23,
        "Grid responsive y reduced motion",
        "col-12 col-sm-6 col-lg-4 col-xl-3" in sources["js"]
        and "@media (max-width: 767.98px)" in sources["css"]
        and "@media (max-width: 575.98px)" in sources["css"]
        and "@media (prefers-reduced-motion: reduce)" in sources["css"],
    )

    expected_product_fields = {
        "id",
        "code",
        "name",
        "description",
        "category_id",
        "category",
        "unit",
        "current_stock",
        "minimum_stock",
        "purchase_price",
        "sale_price",
        "image_url",
        "is_active",
        "is_low_stock",
        "created_at",
        "updated_at",
    }
    check(
        24,
        "GET /api/products devuelve JSON con contrato real",
        products_response.status_code == 200
        and products_response.headers.get("Content-Type", "").startswith("application/json")
        and isinstance(products_data, dict)
        and {"count", "items"}.issubset(products_data)
        and (not products or expected_product_fields.issubset(products[0])),
    )

    categories_response = admin.get(f"{BASE_URL}/api/categories", timeout=TIMEOUT)
    categories_data = get_json(categories_response)
    categories = (
        categories_data.get("items", [])
        if isinstance(categories_data, dict)
        and isinstance(categories_data.get("items"), list)
        else []
    )
    check(
        25,
        "GET /api/categories devuelve JSON con campos reales",
        categories_response.status_code == 200
        and categories_response.headers.get("Content-Type", "").startswith("application/json")
        and isinstance(categories_data, dict)
        and {"count", "items"}.issubset(categories_data)
        and (
            not categories
            or {"id", "name", "description", "created_at"}.issubset(categories[0])
        ),
    )

    check(
        26,
        "Regresión dashboard, productos y categorías",
        request_ok(admin, "/dashboard")
        and request_ok(admin, "/products")
        and request_ok(admin, "/categories"),
    )
    check(
        27,
        "Regresión inventario, notas y perfil",
        request_ok(admin, "/inventory")
        and request_ok(admin, "/delivery-notes")
        and request_ok(admin, "/profile"),
    )
    check(
        28,
        "Regresión de /login",
        requests.get(f"{BASE_URL}/login", timeout=TIMEOUT).status_code == 200,
    )

    after = operational_snapshot(admin)
    check(
        29,
        "Stock, movimientos y notas no cambian",
        before is not None
        and after is not None
        and json_fingerprint(before) == json_fingerprint(after),
    )

    print()
    if CHECKS_RUN != EXPECTED_CHECKS:
        print(
            f"RESULTADO: el script ejecutó {CHECKS_RUN} puntos; "
            f"se esperaban {EXPECTED_CHECKS}."
        )
        return 1
    if FAILURES:
        print(f"RESULTADO: {FAILURES} DE {EXPECTED_CHECKS} PUNTOS FALLARON")
        return 1
    print(f"RESULTADO: LOS {EXPECTED_CHECKS} PUNTOS PASARON")
    return 0


if __name__ == "__main__":
    sys.exit(main())
