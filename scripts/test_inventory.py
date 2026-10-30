#!/usr/bin/env python3
"""Pruebas del frontend web de inventario (página + API consumida).

Requiere servidor en http://localhost:5000 y usuarios semilla.
Uso: python scripts/test_inventory.py
"""
import os
import sys

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

BASE = "http://localhost:5000"
FALLIDOS = 0
TEST_CODE = f"TEST-INV-{os.getpid()}"


def check(name, expected, actual, extra=""):
    global FALLIDOS
    ok = expected == actual
    tag = "OK" if ok else "FALLO"
    if not ok:
        FALLIDOS += 1
    print(f"[{tag}] {name} -> {actual} {extra}")


def login(identifier, password):
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json={"identifier": identifier, "password": password})
    if r.status_code != 200:
        print(f"[SETUP-ERROR] login {identifier} -> {r.status_code}")
        sys.exit(1)
    return s


def main():
    admin = login("admin", "admin123")
    inventario = login("inventario1", "inventario123")
    vendedor = login("vendedor1", "vendedor123")

    # 1. Sin login
    r = requests.get(f"{BASE}/inventory", allow_redirects=False)
    check("1. /inventory sin login -> 302", True, r.status_code == 302 and "/login" in (r.headers.get("Location") or ""))

    # 2-4. Acceso por rol
    r = admin.get(f"{BASE}/inventory")
    check("2a. admin /inventory -> 200", 200, r.status_code)
    check("2b. admin ve botones movimiento", True, "Nueva entrada" in r.text and "Nuevo ajuste" in r.text)

    r = inventario.get(f"{BASE}/inventory")
    check("3. inventario /inventory -> 200", 200, r.status_code)

    r = vendedor.get(f"{BASE}/inventory", allow_redirects=False)
    check("4a. vendedor -> 403", True, r.status_code == 403 and "No tiene permisos para acceder a esta sección." in (r.text or ""))
    r = vendedor.get(f"{BASE}/api/inventory/movements")
    check("4b. vendedor GET movements -> 403", 403, r.status_code)
    r = vendedor.post(f"{BASE}/api/inventory/entry", json={"product_id": 1, "quantity": 1})
    check("4c. vendedor POST entry -> 403", 403, r.status_code)

    # Producto TEST
    cats = admin.get(f"{BASE}/api/categories").json()
    cat_id = cats["items"][0]["id"]
    r = admin.post(f"{BASE}/api/products", json={
        "code": TEST_CODE, "name": "Producto inventario (TEST)",
        "category_id": cat_id, "current_stock": 10, "minimum_stock": 5,
    })
    check("setup. producto TEST creado", 201, r.status_code)
    prod_id = r.json()["id"]
    initial_stock = r.json()["current_stock"]

    # 5. Tabla movimientos carga
    r = admin.get(f"{BASE}/api/inventory/movements")
    data = r.json()
    check("5. GET /api/inventory/movements -> 200 con items", True, r.status_code == 200 and "items" in data)

    # 6. Entrada válida
    r = admin.post(f"{BASE}/api/inventory/entry", json={"product_id": prod_id, "quantity": 3, "reason": "TEST entrada"})
    check("6a. entrada valida -> 201", 201, r.status_code)
    check("6b. stock aumenta 10->13", 13, r.json()["product"]["current_stock"])

    # 7. Salida válida
    r = admin.post(f"{BASE}/api/inventory/exit", json={"product_id": prod_id, "quantity": 2, "reason": "TEST salida"})
    check("7a. salida valida -> 201", 201, r.status_code)
    check("7b. stock disminuye 13->11", 11, r.json()["product"]["current_stock"])

    # 8. Salida mayor al stock -> 409
    r = admin.post(f"{BASE}/api/inventory/exit", json={"product_id": prod_id, "quantity": 999})
    check("8. salida excede stock -> 409", 409, r.status_code)

    # 9. Ajuste válido
    r = admin.post(f"{BASE}/api/inventory/adjustment", json={
        "product_id": prod_id, "new_stock": 20, "reason": "TEST ajuste inventario",
    })
    check("9a. ajuste valido -> 201", 201, r.status_code)
    check("9b. stock queda en 20", 20, r.json()["product"]["current_stock"])

    # 10. Cantidad cero
    r = admin.post(f"{BASE}/api/inventory/entry", json={"product_id": prod_id, "quantity": 0})
    check("10a. cantidad cero -> 400", 400, r.status_code)
    r = admin.post(f"{BASE}/api/inventory/exit", json={"product_id": prod_id, "quantity": -1})
    check("10b. cantidad negativa -> 400", 400, r.status_code)

    # 11. Producto inexistente
    r = admin.post(f"{BASE}/api/inventory/entry", json={"product_id": 999999, "quantity": 1})
    check("11. producto inexistente -> 404", 404, r.status_code)

    # 12. Bajo stock
    r = admin.get(f"{BASE}/api/inventory/low-stock")
    check("12. GET low-stock -> 200 JSON", True, r.status_code == 200 and "items" in r.json())
    r = admin.get(f"{BASE}/inventory")
    check("12b. pagina contiene seccion bajo stock", True, "Productos bajo stock" in r.text)

    # 13-17. No regresión
    r = admin.get(f"{BASE}/dashboard")
    check("13. dashboard OK", True, r.status_code == 200 and "Dashboard de reportes" in r.text)
    r = admin.get(f"{BASE}/products")
    check("14. productos OK", True, r.status_code == 200 and "Productos" in r.text)
    r = admin.get(f"{BASE}/categories")
    check("14b. categorias OK", True, r.status_code == 200 and "Categor" in r.text)
    r = admin.get(f"{BASE}/profile")
    check("15. perfil OK", 200, r.status_code)
    r = admin.get(f"{BASE}/api/inventory/movements")
    check("16. API sigue JSON", True, r.status_code == 200 and isinstance(r.json(), dict))
    r = admin.get(f"{BASE}/api/reports/dashboard-summary")
    check("17. reportes OK", True, r.status_code == 200 and isinstance(r.json(), dict))

    # Limpieza
    cleanup_script = os.path.join(os.path.dirname(__file__), "cleanup_test_products.py")
    if os.path.isfile(cleanup_script):
        import subprocess
        subprocess.run([sys.executable, cleanup_script], check=False)

    print()
    if FALLIDOS == 0:
        print("RESULTADO: TODAS LAS PRUEBAS PASARON")
    else:
        print(f"RESULTADO: {FALLIDOS} PRUEBA(S) FALLARON")
    sys.exit(FALLIDOS)


if __name__ == "__main__":
    main()
