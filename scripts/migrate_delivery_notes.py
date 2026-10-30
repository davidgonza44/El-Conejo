"""Migración del módulo de notas de entrega (Incremento 5).

Las tablas delivery_notes y delivery_note_items existían con un esquema
antiguo (client_name, total, created_by, subtotal...) y nunca se usaron.
Este script las recrea con el esquema nuevo.

Idempotencia:
- Si las tablas ya tienen el esquema nuevo (columna customer_name), no hace nada.
- Si tienen el esquema antiguo y están VACÍAS, las elimina y las vuelve a
  crear desde los modelos SQLAlchemy (db.create_all valida previamente qué
  tablas existen: equivale a CREATE TABLE IF NOT EXISTS).
- Si tienen el esquema antiguo pero contienen datos, aborta sin tocar nada.

Seguridad:
- SOLO opera sobre delivery_notes y delivery_note_items. No toca products,
  users, stock_movements ni ninguna otra tabla.

Verificaciones al final:
- Que los tipos de created_by_user_id / cancelled_by_user_id coincidan con users.id.
- Que el tipo de product_id coincida con products.id.
- Que existan los índices explícitos requeridos.

Uso:
    python scripts/migrate_delivery_notes.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import DeliveryNote, DeliveryNoteItem  # noqa: F401 (registra modelos)

REQUIRED_INDEXES = {
    "delivery_notes": ["status", "created_at", "created_by_user_id", "note_number"],
    "delivery_note_items": ["delivery_note_id", "product_id"],
}

# (tabla, columna FK) -> (tabla referencia, columna referencia)
FK_TYPE_CHECKS = [
    ("delivery_notes", "created_by_user_id", "users", "id"),
    ("delivery_notes", "cancelled_by_user_id", "users", "id"),
    ("delivery_note_items", "product_id", "products", "id"),
    ("delivery_note_items", "delivery_note_id", "delivery_notes", "id"),
]


def _column_type(table: str, column: str) -> str:
    """Tipo de dato de una columna según information_schema (ej: 'int')."""
    row = db.session.execute(
        text(
            "SELECT DATA_TYPE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row[0] if row else "(no existe)"


def _indexed_columns(table: str) -> set[str]:
    """Columnas que son la primera columna de algún índice de la tabla."""
    rows = db.session.execute(
        text(
            "SELECT COLUMN_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND SEQ_IN_INDEX = 1"
        ),
        {"t": table},
    ).all()
    return {row[0] for row in rows}


def _verify() -> bool:
    ok = True

    print("\nVerificación de tipos de claves foráneas:")
    for table, column, ref_table, ref_column in FK_TYPE_CHECKS:
        col_type = _column_type(table, column)
        ref_type = _column_type(ref_table, ref_column)
        match = col_type == ref_type
        ok = ok and match
        status = "OK" if match else "ERROR"
        print(f"  [{status}] {table}.{column} ({col_type}) == {ref_table}.{ref_column} ({ref_type})")

    print("\nVerificación de índices explícitos:")
    for table, columns in REQUIRED_INDEXES.items():
        indexed = _indexed_columns(table)
        for column in columns:
            present = column in indexed
            ok = ok and present
            status = "OK" if present else "ERROR"
            print(f"  [{status}] índice sobre {table}.{column}")

    return ok


def main() -> None:
    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())

        notes_exists = "delivery_notes" in existing_tables
        items_exists = "delivery_note_items" in existing_tables

        if notes_exists:
            columns = {c["name"] for c in inspector.get_columns("delivery_notes")}
            if "customer_name" in columns:
                print("[OK] delivery_notes ya tiene el esquema nuevo. Nada que recrear.")
                sys.exit(0 if _verify() else 1)

            # Esquema antiguo: solo se recrea si las tablas están vacías.
            note_rows = db.session.execute(
                text("SELECT COUNT(*) FROM delivery_notes")
            ).scalar()
            item_rows = 0
            if items_exists:
                item_rows = db.session.execute(
                    text("SELECT COUNT(*) FROM delivery_note_items")
                ).scalar()

            if note_rows or item_rows:
                print(
                    f"[ABORTADO] delivery_notes tiene {note_rows} filas y "
                    f"delivery_note_items tiene {item_rows}. No se elimina nada; "
                    "revise los datos manualmente antes de migrar."
                )
                sys.exit(1)

            print("[INFO] Esquema antiguo detectado y tablas vacías. Recreando...")
            db.session.execute(text("DROP TABLE IF EXISTS delivery_note_items"))
            db.session.execute(text("DROP TABLE IF EXISTS delivery_notes"))
            db.session.commit()
        else:
            print("[INFO] Las tablas no existen. Se crearán desde cero.")

        # create_all valida qué tablas existen antes de crear (checkfirst):
        # solo crea las que faltan, nunca modifica ni borra las demás.
        db.create_all()
        print("[OK] Tablas delivery_notes y delivery_note_items creadas con el esquema nuevo.")

        inspector = inspect(db.engine)
        for table in ("delivery_notes", "delivery_note_items"):
            cols = ", ".join(c["name"] for c in inspector.get_columns(table))
            print(f"  - {table}: {cols}")

        sys.exit(0 if _verify() else 1)


if __name__ == "__main__":
    main()
