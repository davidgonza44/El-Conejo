#!/usr/bin/env python3
"""Marca opcional del producto. SQLite desechable; no toca ferreteria_conejo_tesis."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from test_products_core import create_via_api, modify_via_api, setup_app  # noqa: E402
from app.extensions import db  # noqa: E402

FAILURES = 0


def check(name, condition, detail=""):
    global FAILURES
    if not condition:
        FAILURES += 1
    print(f"[{'OK' if condition else 'FALLO'}] {name}" + (f" — {detail}" if detail else ""))


def main():
    folder = Path(tempfile.mkdtemp(prefix="product-brand-"))
    try:
        app, admin, _inventario, _vendedor, ids = setup_app(folder)
        category = ids["construccion"]
        base = {"name": "Taladro", "category_id": category, "unit": "unidad", "purchase_price": "8.00", "sale_price": "12.00"}
        plain = create_via_api(admin, **base, code="MAR-0")
        check("crear sin marca", plain.status_code == 201 and plain.json["brand"] is None, str(plain.json))
        branded = create_via_api(admin, **base, code="MAR-1", brand="  Bosch  ")
        check("crear con marca recortada", branded.status_code == 201 and branded.json["brand"] == "Bosch", str(branded.json))
        blank = create_via_api(admin, **base, code="MAR-2", brand="   ")
        check("marca en blanco queda nula", blank.status_code == 201 and blank.json["brand"] is None)
        too_long = create_via_api(admin, **base, code="MAR-3", brand="B" * 101)
        check("más de 100 caracteres se rechaza", too_long.status_code == 400, too_long.get_data(as_text=True))
        edited = modify_via_api(admin, plain.json['id'], {"brand": "Makita"})
        check("aprobar marca", edited.status_code == 200 and edited.json["brand"] == "Makita")
        cleared = modify_via_api(admin, plain.json['id'], {"brand": ""})
        check("borrar marca deja NULL", cleared.status_code == 200 and cleared.json["brand"] is None)
        kept = modify_via_api(admin, branded.json['id'], {"name": "Taladro percutor"})
        absent = modify_via_api(admin, branded.json['id'], {"brand": None, "description": "Sin marca en la fuente"})
        check("ausencia o null no borra la marca local",
              kept.status_code == 200 and kept.json["brand"] == "Bosch" and kept.json["name"] == "Taladro percutor"
              and absent.status_code == 200 and absent.json["brand"] == "Bosch")
        detail = admin.get(f"/api/products/{branded.json['id']}")
        check("detalle incluye marca", detail.status_code == 200 and detail.json["brand"] == "Bosch" and detail.json["purchase_price"] == 8.0)
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
    finally:
        pass
    if FAILURES:
        raise SystemExit(1)
    print("PASS: marca opcional")


if __name__ == "__main__":
    main()
