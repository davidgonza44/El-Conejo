#!/usr/bin/env python3
"""Inspección de datos TEST WEB / TEST-DN en BD (solo lectura)."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app import create_app
from app.extensions import db

app = create_app()

with app.app_context():
    notes = db.session.execute(text("""
        SELECT id, note_number, customer_name, status, total_amount, created_at
        FROM delivery_notes
        WHERE customer_name LIKE '%TEST WEB%'
        ORDER BY id
    """)).mappings().all()

    print("=== NOTAS TEST WEB ===")
    print(f"count={len(notes)}")
    for n in notes:
        print(dict(n))

    note_ids = [n["id"] for n in notes]
    if note_ids:
        placeholders = ",".join(str(i) for i in note_ids)
        items = db.session.execute(text(f"""
            SELECT dni.id, dni.delivery_note_id, dni.product_id, dni.product_code,
                   dni.quantity, dn.status AS note_status
            FROM delivery_note_items dni
            JOIN delivery_notes dn ON dn.id = dni.delivery_note_id
            WHERE dni.delivery_note_id IN ({placeholders})
        """)).mappings().all()
    else:
        items = []

    print("\n=== DELIVERY_NOTE_ITEMS asociados ===")
    print(f"count={len(items)}")
    for i in items:
        print(dict(i))

    products = db.session.execute(text("""
        SELECT id, code, name, current_stock, is_active
        FROM products
        WHERE code LIKE 'TEST-DN-%' OR code LIKE 'TEST-%'
        ORDER BY id
    """)).mappings().all()

    print("\n=== PRODUCTOS TEST ===")
    print(f"count={len(products)}")
    for p in products:
        print(dict(p))

    prod_ids = [p["id"] for p in products]
    if prod_ids:
        placeholders = ",".join(str(i) for i in prod_ids)
        movements = db.session.execute(text(f"""
            SELECT id, product_id, movement_type, quantity, previous_stock, new_stock, reason, created_at
            FROM stock_movements
            WHERE product_id IN ({placeholders})
            ORDER BY id
        """)).mappings().all()
    else:
        movements = []

    print("\n=== STOCK_MOVEMENTS asociados a productos TEST ===")
    print(f"count={len(movements)}")
    for m in movements:
        print(dict(m))

    issued = sum(1 for n in notes if n["status"] == "issued")
    cancelled = sum(1 for n in notes if n["status"] == "cancelled")
    print("\n=== RESUMEN ESTADOS ===")
    print(f"issued={issued}, cancelled={cancelled}")
