#!/usr/bin/env python3
"""Pruebas del frontend web de notas de entrega (página + API).

Requiere servidor en http://localhost:5000 y usuarios semilla.
Uso: python scripts/test_delivery_notes.py
"""
import os
import sys

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

BASE = "http://localhost:5000"
FALLIDOS = 0
TEST_CUSTOMER = f"Cliente TEST WEB {os.getpid()}"


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

    # 1-4 Acceso
    r = requests.get(f"{BASE}/delivery-notes", allow_redirects=False)
    check("1. sin login -> 302 /login", True, r.status_code == 302 and "/login" in (r.headers.get("Location") or ""))

    r = admin.get(f"{BASE}/delivery-notes")
    check("2a. admin /delivery-notes -> 200", 200, r.status_code)
    check("2b. admin ve Nueva nota", True, "Nueva nota de entrega" in r.text)

    r = vendedor.get(f"{BASE}/delivery-notes")
    check("3a. vendedor -> 200", 200, r.status_code)
    check("3b. vendedor ve Nueva nota", True, "Nueva nota de entrega" in r.text)

    r = inventario.get(f"{BASE}/delivery-notes")
    check("4a. inventario -> 200", 200, r.status_code)
    check("4b. inventario NO ve Nueva nota", False, "Nueva nota de entrega" in r.text)

    # Producto con stock para pruebas
    cats = admin.get(f"{BASE}/api/categories").json()
    cat_id = cats["items"][0]["id"]
    code = f"TEST-DN-{os.getpid()}"
    r = admin.post(f"{BASE}/api/products", json={
        "code": code, "name": "Producto nota TEST", "category_id": cat_id,
        "current_stock": 50, "minimum_stock": 0, "sale_price": 10.0,
    })
    prod_id = r.json()["id"]
    stock_before = r.json()["current_stock"]

    # 5 Listado
    r = admin.get(f"{BASE}/api/delivery-notes")
    check("5. listado -> 200 con items", True, r.status_code == 200 and "items" in r.json())

    # 6 Filtros
    r = admin.get(f"{BASE}/api/delivery-notes", params={"status": "issued"})
    check("6a. filtro status=issued", 200, r.status_code)
    r = admin.get(f"{BASE}/api/delivery-notes", params={"customer_name": "TEST", "date_from": "2020-01-01"})
    check("6b. filtro cliente/fecha", 200, r.status_code)

    # 10-12 validaciones crear
    r = admin.post(f"{BASE}/api/delivery-notes", json={"items": [{"product_id": prod_id, "quantity": 1}]})
    check("10. sin cliente -> 400", 400, r.status_code)
    r = admin.post(f"{BASE}/api/delivery-notes", json={"customer_name": TEST_CUSTOMER, "items": []})
    check("11. sin productos -> 400", 400, r.status_code)
    r = admin.post(f"{BASE}/api/delivery-notes", json={
        "customer_name": TEST_CUSTOMER,
        "items": [{"product_id": prod_id, "quantity": 0}],
    })
    check("12. cantidad 0 -> 400", 400, r.status_code)

    # 8-9 Crear nota válida y verificar stock
    qty = 5
    r = admin.post(f"{BASE}/api/delivery-notes", json={
        "customer_name": TEST_CUSTOMER,
        "customer_document": "V-TEST",
        "items": [{"product_id": prod_id, "quantity": qty}],
    })
    check("8a. crear nota valida -> 201", 201, r.status_code)
    note = r.json()["delivery_note"]
    note_id = note["id"]
    note_number = note["note_number"]
    check("8b. numero de nota generado", True, note_number.startswith("NE-"))

    r = admin.get(f"{BASE}/api/products/{prod_id}")
    stock_after_create = r.json()["current_stock"]
    check("9. crear nota descuenta stock", stock_before - qty, stock_after_create)

    # 7 Detalle
    r = admin.get(f"{BASE}/api/delivery-notes/{note_id}")
    check("7. detalle -> 200 con items", True, r.status_code == 200 and len(r.json().get("items", [])) == 1)

    # 13 Stock insuficiente
    r = admin.post(f"{BASE}/api/delivery-notes", json={
        "customer_name": TEST_CUSTOMER,
        "items": [{"product_id": prod_id, "quantity": 9999}],
    })
    check("13. stock insuficiente -> 409", 409, r.status_code)

    # 14-15 Cancelar y devolver stock
    r = inventario.post(f"{BASE}/api/delivery-notes/{note_id}/cancel", json={})
    check("14. inventario cancela -> 200", 200, r.status_code)
    r = admin.get(f"{BASE}/api/products/{prod_id}")
    stock_after_cancel = r.json()["current_stock"]
    check("15. cancelar devuelve stock", stock_before, stock_after_cancel)

    # 16 Cancelar dos veces
    r = inventario.post(f"{BASE}/api/delivery-notes/{note_id}/cancel", json={})
    check("16. cancelar dos veces -> 409", 409, r.status_code)

    # 17 Vendedor no puede cancelar
    r2 = vendedor.post(f"{BASE}/api/delivery-notes", json={
        "customer_name": f"{TEST_CUSTOMER} 2",
        "items": [{"product_id": prod_id, "quantity": 1}],
    })
    note2_id = r2.json()["delivery_note"]["id"]
    r = vendedor.post(f"{BASE}/api/delivery-notes/{note2_id}/cancel", json={})
    check("17. vendedor cancelar -> 403", 403, r.status_code)
    # Limpiar nota 2 con inventario
    inventario.post(f"{BASE}/api/delivery-notes/{note2_id}/cancel", json={})

    # 18-24 No regresión
    r = admin.get(f"{BASE}/dashboard")
    check("18. dashboard OK", True, r.status_code == 200 and "Dashboard de reportes" in r.text)
    r = admin.get(f"{BASE}/products")
    check("19. productos OK", 200, r.status_code)
    r = admin.get(f"{BASE}/inventory")
    check("20. inventario OK", 200, r.status_code)
    r = admin.get(f"{BASE}/profile")
    check("21. perfil OK", 200, r.status_code)
    r = admin.get(f"{BASE}/login")
    check("22. auth web OK", 200, r.status_code)
    r = admin.get(f"{BASE}/api/delivery-notes")
    check("23. API JSON", True, r.status_code == 200 and isinstance(r.json(), dict))
    r = admin.get(f"{BASE}/api/reports/dashboard-summary")
    check("24. reportes OK", True, r.status_code == 200)

    # 25 Datos TEST identificados (customer_name contiene TEST WEB)
    r = admin.get(f"{BASE}/api/delivery-notes", params={"customer_name": "TEST WEB"})
    test_count = r.json()["count"]
    check("25. notas TEST identificables", True, test_count >= 1, f"(count={test_count})")

    # Limpieza producto TEST (notas quedan en BD como histórico TEST)
    cleanup = os.path.join(os.path.dirname(__file__), "cleanup_test_products.py")
    if os.path.isfile(cleanup):
        import subprocess
        subprocess.run([sys.executable, cleanup], check=False)

    print()
    if FALLIDOS == 0:
        print("RESULTADO: TODAS LAS PRUEBAS PASARON")
    else:
        print(f"RESULTADO: {FALLIDOS} PRUEBA(S) FALLARON")
    sys.exit(FALLIDOS)


if __name__ == "__main__":
    main()
