#!/usr/bin/env python3
"""Verificación final de cierre del módulo web /delivery-notes + regresión.

Requiere servidor en http://localhost:5000.
Uso: python scripts/verify_delivery_notes_closure.py
"""
import os
import subprocess
import sys

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

BASE = "http://localhost:5000"
FALLIDOS = 0


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
    print("=== VERIFICACIÓN DE CIERRE: NOTAS DE ENTREGA ===\n")

    admin = login("admin", "admin123")
    inventario = login("inventario1", "inventario123")
    vendedor = login("vendedor1", "vendedor123")

    # --- Acceso y permisos UI ---
    r = requests.get(f"{BASE}/delivery-notes", allow_redirects=False)
    check("sin sesión -> redirect /login", True, r.status_code == 302 and "/login" in (r.headers.get("Location") or ""))

    r = admin.get(f"{BASE}/delivery-notes")
    check("admin /delivery-notes -> 200", 200, r.status_code)
    check("admin ve botón Nueva nota", True, "Nueva nota de entrega" in r.text)
    check("admin CAN_CANCEL=true", True, "CAN_CANCEL=true" in r.text.replace(" ", ""))

    r = vendedor.get(f"{BASE}/delivery-notes")
    check("vendedor /delivery-notes -> 200", 200, r.status_code)
    check("vendedor ve botón Nueva nota", True, "Nueva nota de entrega" in r.text)
    check("vendedor CAN_CANCEL=false", True, "CAN_CANCEL=false" in r.text.replace(" ", ""))

    r = inventario.get(f"{BASE}/delivery-notes")
    check("inventario /delivery-notes -> 200", 200, r.status_code)
    check("inventario NO ve Nueva nota", False, "Nueva nota de entrega" in r.text)
    check("inventario CAN_CREATE=false", True, "CAN_CREATE=false" in r.text.replace(" ", ""))

    # --- Producto TEST temporal ---
    cats = admin.get(f"{BASE}/api/categories").json()
    cat_id = cats["items"][0]["id"]
    code = f"TEST-DN-{os.getpid()}"
    r = admin.post(f"{BASE}/api/products", json={
        "code": code, "name": "Producto nota TEST cierre", "category_id": cat_id,
        "current_stock": 50, "minimum_stock": 0, "sale_price": 10.0,
    })
    if r.status_code not in (200, 201):
        print(f"[SETUP-ERROR] crear producto TEST -> {r.status_code} {r.text}")
        sys.exit(1)
    prod_id = r.json()["id"]
    stock_before = r.json()["current_stock"]
    test_customer = f"Cliente TEST WEB cierre {os.getpid()}"

    # --- Errores HTTP API ---
    r = admin.post(f"{BASE}/api/delivery-notes", json={"items": [{"product_id": prod_id, "quantity": 1}]})
    check("400 sin cliente", 400, r.status_code)

    r = requests.get(f"{BASE}/api/delivery-notes", allow_redirects=False)
    check("401 API sin sesión", 401, r.status_code)

    r = inventario.post(f"{BASE}/api/delivery-notes/999999/cancel", json={})
    check("404 cancel nota inexistente", 404, r.status_code)

    r = admin.post(f"{BASE}/api/delivery-notes", json={
        "customer_name": test_customer,
        "items": [{"product_id": prod_id, "quantity": 9999}],
    })
    check("409 stock insuficiente", 409, r.status_code)

    # --- Crear, stock, cancelar ---
    qty = 3
    r = admin.post(f"{BASE}/api/delivery-notes", json={
        "customer_name": test_customer,
        "items": [{"product_id": prod_id, "quantity": qty}],
    })
    check("crear nota -> 201", 201, r.status_code)
    note_id = r.json()["delivery_note"]["id"]

    r = admin.get(f"{BASE}/api/products/{prod_id}")
    check("crear descuenta stock", stock_before - qty, r.json()["current_stock"])

    r = inventario.post(f"{BASE}/api/delivery-notes/{note_id}/cancel", json={})
    check("inventario cancela -> 200", 200, r.status_code)

    r = admin.get(f"{BASE}/api/products/{prod_id}")
    check("cancelar devuelve stock", stock_before, r.json()["current_stock"])

    r = inventario.post(f"{BASE}/api/delivery-notes/{note_id}/cancel", json={})
    check("cancelar dos veces -> 409", 409, r.status_code)

    r2 = vendedor.post(f"{BASE}/api/delivery-notes", json={
        "customer_name": f"{test_customer} v2",
        "items": [{"product_id": prod_id, "quantity": 1}],
    })
    note2_id = r2.json()["delivery_note"]["id"]
    r = vendedor.post(f"{BASE}/api/delivery-notes/{note2_id}/cancel", json={})
    check("vendedor cancelar -> 403", 403, r.status_code)
    inventario.post(f"{BASE}/api/delivery-notes/{note2_id}/cancel", json={})

    # --- Regresión ---
    print("\n=== REGRESIÓN ===")
    r = admin.get(f"{BASE}/dashboard")
    check("reg /dashboard", True, r.status_code == 200 and "Dashboard de reportes" in r.text)
    r = admin.get(f"{BASE}/products")
    check("reg /products", 200, r.status_code)
    r = admin.get(f"{BASE}/categories")
    check("reg /categories", 200, r.status_code)
    r = admin.get(f"{BASE}/inventory")
    check("reg /inventory", 200, r.status_code)
    r = admin.get(f"{BASE}/profile")
    check("reg /profile", 200, r.status_code)
    r = admin.get(f"{BASE}/login")
    check("reg /login", 200, r.status_code)

    r = admin.get(f"{BASE}/api/reports/dashboard-summary")
    check("reg API dashboard-summary JSON", True, r.status_code == 200 and isinstance(r.json(), dict))
    r = admin.get(f"{BASE}/api/products")
    check("reg API products JSON", True, r.status_code == 200 and "items" in r.json())
    r = admin.get(f"{BASE}/api/inventory/movements")
    check("reg API inventory/movements JSON", True, r.status_code == 200 and isinstance(r.json(), dict))
    r = admin.get(f"{BASE}/api/delivery-notes")
    check("reg API delivery-notes JSON", True, r.status_code == 200 and "items" in r.json())

    print()
    if FALLIDOS == 0:
        print("CIERRE: VERIFICACIÓN EXITOSA")
    else:
        print(f"CIERRE: {FALLIDOS} VERIFICACIÓN(ES) FALLARON")
    sys.exit(FALLIDOS)


if __name__ == "__main__":
    main()
