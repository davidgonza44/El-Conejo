"""Elimina productos de prueba con código TEST-*."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from app.extensions import db
from sqlalchemy import text

app = create_app()
with app.app_context():
    # Borrar movimientos asociados a productos TEST antes de eliminar el producto (FK).
    db.session.execute(text(
        "DELETE sm FROM stock_movements sm "
        "INNER JOIN products p ON p.id = sm.product_id "
        "WHERE p.code LIKE 'TEST-%'"
    ))
    r = db.session.execute(text("DELETE FROM products WHERE code LIKE 'TEST-%'"))
    db.session.commit()
    print(f"productos_borrados={r.rowcount}")
