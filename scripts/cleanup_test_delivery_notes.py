#!/usr/bin/env python3
"""Limpieza segura de datos TEST WEB y productos TEST-DN / TEST-*.

NO ejecutar en producción sin revisar conteos previos.
Borra SOLO:
  - delivery_notes con customer_name LIKE '%TEST WEB%'
  - delivery_note_items de esas notas
  - stock_movements de productos con code LIKE 'TEST-%'
  - productos con code LIKE 'TEST-%'

Uso: python scripts/cleanup_test_delivery_notes.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app import create_app
from app.extensions import db

NOTE_FILTER = "customer_name LIKE '%TEST WEB%'"
PRODUCT_FILTER = "code LIKE 'TEST-%'"


def count(sql: str) -> int:
    return db.session.execute(text(sql)).scalar() or 0


def main():
    app = create_app()
    with app.app_context():
        print("=== ANTES ===")
        n_notes = count(f"SELECT COUNT(*) FROM delivery_notes WHERE {NOTE_FILTER}")
        n_items = count(f"""
            SELECT COUNT(*) FROM delivery_note_items dni
            INNER JOIN delivery_notes dn ON dn.id = dni.delivery_note_id
            WHERE dn.{NOTE_FILTER}
        """)
        n_products = count(f"SELECT COUNT(*) FROM products WHERE {PRODUCT_FILTER}")
        n_movements = count(f"""
            SELECT COUNT(*) FROM stock_movements sm
            INNER JOIN products p ON p.id = sm.product_id
            WHERE p.{PRODUCT_FILTER}
        """)
        print(f"delivery_notes TEST WEB: {n_notes}")
        print(f"delivery_note_items:   {n_items}")
        print(f"products TEST-*:       {n_products}")
        print(f"stock_movements:       {n_movements}")

        if n_notes == 0 and n_products == 0:
            print("\nNada que limpiar.")
            return

        try:
            # 1) Movimientos de stock de productos TEST (incluye salidas/entradas de notas TEST)
            db.session.execute(text(f"""
                DELETE sm FROM stock_movements sm
                INNER JOIN products p ON p.id = sm.product_id
                WHERE p.{PRODUCT_FILTER}
            """))

            # 2) Ítems de notas TEST WEB
            db.session.execute(text(f"""
                DELETE dni FROM delivery_note_items dni
                INNER JOIN delivery_notes dn ON dn.id = dni.delivery_note_id
                WHERE dn.{NOTE_FILTER}
            """))

            # 3) Notas TEST WEB
            db.session.execute(text(f"DELETE FROM delivery_notes WHERE {NOTE_FILTER}"))

            # 4) Productos TEST
            db.session.execute(text(f"DELETE FROM products WHERE {PRODUCT_FILTER}"))

            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            print(f"\nERROR — rollback: {exc}")
            sys.exit(1)

        print("\n=== DESPUÉS ===")
        print(f"delivery_notes TEST WEB: {count(f'SELECT COUNT(*) FROM delivery_notes WHERE {NOTE_FILTER}')}")
        print(f"delivery_note_items:   {count('SELECT COUNT(*) FROM delivery_note_items dni INNER JOIN delivery_notes dn ON dn.id = dni.delivery_note_id WHERE dn.' + NOTE_FILTER)}")
        print(f"products TEST-*:       {count(f'SELECT COUNT(*) FROM products WHERE {PRODUCT_FILTER}')}")
        print(f"stock_movements:       {count('SELECT COUNT(*) FROM stock_movements sm INNER JOIN products p ON p.id = sm.product_id WHERE p.' + PRODUCT_FILTER)}")
        print("\nLimpieza completada.")


if __name__ == "__main__":
    main()
