#!/usr/bin/env python3
"""Pruebas de imágenes de producto y foto de perfil (20 casos + no-regresión).

Requiere servidor en http://localhost:5000 y usuarios semilla.
Uso: python scripts/test_images_profile.py
"""
import base64
import os
import sys
import tempfile

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

BASE = "http://localhost:5000"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
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
    tmp = tempfile.mkdtemp(prefix="img_test_")
    valida = os.path.join(tmp, "valida.png")
    valida2 = os.path.join(tmp, "valida2.png")
    falsa = os.path.join(tmp, "falsa.png")
    grande = os.path.join(tmp, "grande.png")
    exe_txt = os.path.join(tmp, "script.exe.txt")

    with open(valida, "wb") as f:
        f.write(PNG)
    with open(valida2, "wb") as f:
        f.write(PNG)
    with open(falsa, "wb") as f:
        f.write(b"no soy una imagen")
    with open(exe_txt, "wb") as f:
        f.write(b"MZ fake exe")
    with open(grande, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"0" * (3 * 1024 * 1024))

    admin = login("admin", "admin123")
    inventario = login("inventario1", "inventario123")
    vendedor = login("vendedor1", "vendedor123")

    # --- Página productos: bootstrap shim presente ---
    r = admin.get(f"{BASE}/products")
    check("1. /products contiene shim bootstrap", True, "window.tabler && window.tabler.bootstrap" in r.text)

    # --- Producto TEST ---
    cats = admin.get(f"{BASE}/api/categories").json()
    cat_id = cats["items"][0]["id"]
    code = f"TEST-IMG-{os.getpid()}"
    r = admin.post(
        f"{BASE}/api/products",
        json={"code": code, "name": "Producto imagen (TEST)", "category_id": cat_id},
    )
    check("2. crear producto sin imagen -> 201", 201, r.status_code)
    prod_id = r.json()["id"]
    check("2b. image_url null al crear", None, r.json().get("image_url"))

    # --- Editar sin imagen ---
    r = admin.put(f"{BASE}/api/products/{prod_id}", json={"name": "Producto imagen EDIT (TEST)"})
    check("3. editar producto sin cambiar imagen -> 200", 200, r.status_code)

    # --- Subir imagen ---
    with open(valida, "rb") as f:
        r = admin.post(f"{BASE}/api/products/{prod_id}/image", files={"image": ("valida.png", f, "image/png")})
    check("4. subir imagen valida -> 200", 200, r.status_code)
    image_url = r.json().get("image_url", "")
    check("4b. image_url /media/products/", True, image_url.startswith("/media/products/"))

    r = admin.get(f"{BASE}{image_url}")
    check("5. GET miniatura con sesion -> 200", 200, r.status_code)

    r = requests.get(f"{BASE}{image_url}")
    check("5b. GET miniatura sin sesion -> 401", 401, r.status_code)

    # --- Reemplazar ---
    with open(valida2, "rb") as f:
        r = admin.post(f"{BASE}/api/products/{prod_id}/image", files={"image": ("valida2.png", f, "image/png")})
    image_url2 = r.json().get("image_url", "")
    check("6. reemplazar imagen -> url distinta", True, r.status_code == 200 and image_url2 != image_url)
    r = admin.get(f"{BASE}{image_url}")
    check("6b. imagen anterior borrada -> 404", 404, r.status_code)

    # --- Eliminar imagen producto ---
    r = admin.delete(f"{BASE}/api/products/{prod_id}/image")
    check("7. eliminar imagen producto -> 200", 200, r.status_code)
    check("7b. product.image_url null", None, r.json()["product"].get("image_url"))
    r = admin.get(f"{BASE}{image_url2}")
    check("7c. archivo eliminado -> 404", 404, r.status_code)

    # --- Validaciones ---
    with open(falsa, "rb") as f:
        r = admin.post(f"{BASE}/api/products/{prod_id}/image", files={"image": ("falsa.png", f)})
    check("8. contenido falso -> 400", 400, r.status_code)

    with open(exe_txt, "rb") as f:
        r = admin.post(f"{BASE}/api/products/{prod_id}/image", files={"image": ("script.exe.txt", f)})
    check("8b. extension no permitida -> 400", 400, r.status_code)

    with open(grande, "rb") as f:
        r = admin.post(f"{BASE}/api/products/{prod_id}/image", files={"image": ("grande.png", f)})
    check("9. archivo >2MB -> 400 o 413", True, r.status_code in (400, 413), f"(code={r.status_code})")

    with open(valida, "rb") as f:
        r = vendedor.post(f"{BASE}/api/products/{prod_id}/image", files={"image": ("valida.png", f)})
    check("10. vendedor sube imagen -> 403", 403, r.status_code)

    with open(valida, "rb") as f:
        r = inventario.post(f"{BASE}/api/products/{prod_id}/image", files={"image": ("valida.png", f, "image/png")})
    check("12. inventario sube imagen -> 200", 200, r.status_code)

    # --- Foto de perfil ---
    with open(valida, "rb") as f:
        r = admin.post(f"{BASE}/api/auth/me/profile-photo", files={"image": ("valida.png", f, "image/png")})
    check("13. usuario sube foto perfil -> 200", 200, r.status_code)
    photo_url = r.json().get("profile_photo_url", "")
    check("13b. profile_photo_url /media/users/", True, photo_url.startswith("/media/users/"))

    r = admin.get(f"{BASE}/profile")
    check("15a. /profile muestra pagina", 200, r.status_code)
    check("15b. navbar muestra img si hay foto", True, photo_url.split("/")[-1] in r.text or photo_url in r.text)

    r = admin.delete(f"{BASE}/api/auth/me/profile-photo")
    check("14. eliminar foto perfil -> 200", 200, r.status_code)
    check("14b. profile_photo_url null", None, r.json()["user"].get("profile_photo_url"))

    r = admin.get(f"{BASE}/profile")
    check("15c. sin foto muestra iniciales en pagina", True, "avatar-xl bg-primary-lt" in r.text)

    # --- No regresión ---
    r = admin.get(f"{BASE}/api/products/{prod_id}")
    check("16. GET /api/products/<id> sigue JSON", True, r.status_code == 200 and "code" in r.json())

    r = admin.get(f"{BASE}/dashboard")
    check("17. dashboard sigue funcionando", True, r.status_code == 200 and "Dashboard de reportes" in r.text)

    r = admin.get(f"{BASE}/login")
    check("18. auth web /login -> 200", 200, r.status_code)

    # --- Limpieza ---
    admin.delete(f"{BASE}/api/products/{prod_id}/image")
    cleanup = f"""
import sys
sys.path.insert(0, '.')
from app import create_app
from app.extensions import db
from sqlalchemy import text
app = create_app()
with app.app_context():
    db.session.execute(text("DELETE FROM products WHERE code LIKE 'TEST-IMG-%'"))
    db.session.execute(text("UPDATE users SET profile_photo_filename = NULL WHERE username = 'admin'"))
    db.session.commit()
"""
    os.system(f'"{sys.executable}" -c "{cleanup.replace(chr(10), "; ")}"')

    uploads_products = os.path.join(os.path.dirname(__file__), "..", "uploads", "products")
    leftover = len(os.listdir(uploads_products)) if os.path.isdir(uploads_products) else 0
    check("20. uploads/products limpio de TEST", 0, leftover)

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FALLIDOS == 0:
        print("RESULTADO: TODAS LAS PRUEBAS PASARON")
    else:
        print(f"RESULTADO: {FALLIDOS} PRUEBA(S) FALLARON")
    sys.exit(FALLIDOS)


if __name__ == "__main__":
    main()
