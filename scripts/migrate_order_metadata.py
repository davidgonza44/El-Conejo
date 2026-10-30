"""Migración aditiva: metadatos del pedido (Orders Core v1).

Qué hace (solo si la columna aún no existe, consultando information_schema):
- ALTER TABLE orders ADD COLUMN priority VARCHAR(20) NOT NULL DEFAULT 'medium'
- ALTER TABLE orders ADD COLUMN payment_status VARCHAR(20) NOT NULL DEFAULT 'pending'
- ALTER TABLE orders ADD COLUMN contact_phone VARCHAR(30) NULL
- ALTER TABLE orders ADD COLUMN delivery_address VARCHAR(255) NULL
- ALTER TABLE orders ADD COLUMN notes VARCHAR(500) NULL
Las filas existentes quedan con priority='medium', payment_status='pending' y el resto NULL.
NO borra ni recrea tablas; NO toca products, stock_movements, sales, customers ni order_lines;
NO inserta ni actualiza filas. Idempotente: la segunda ejecución no hace nada.

Requiere que exista orders (scripts/migrate_orders.py).
    python scripts/migrate_order_metadata.py --database ferreteria_conejo_e2e
    python scripts/migrate_order_metadata.py --database ferreteria_conejo
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pymysql

from app.config import Config

COLUMNS = (
    ("priority", "VARCHAR(20) NOT NULL DEFAULT 'medium'"),
    ("payment_status", "VARCHAR(20) NOT NULL DEFAULT 'pending'"),
    ("contact_phone", "VARCHAR(30) NULL"),
    ("delivery_address", "VARCHAR(255) NULL"),
    ("notes", "VARCHAR(500) NULL"),
)


def column_exists(cursor, database, table, column):
    cursor.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s",
                   (database, table, column))
    return cursor.fetchone()[0] > 0


def migrate(connection, database):
    added = []
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE()")
        if cursor.fetchone()[0] != database:
            raise RuntimeError("La conexión no apunta a la base indicada.")
        cursor.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME='orders'", (database,))
        if cursor.fetchone()[0] == 0:
            raise RuntimeError("Falta la tabla orders: ejecute primero scripts/migrate_orders.py.")
        for column, definition in COLUMNS:
            if not column_exists(cursor, database, "orders", column):
                cursor.execute(f"ALTER TABLE orders ADD COLUMN {column} {definition}")
                added.append(column)
        connection.commit()
    return added


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, choices=("ferreteria_conejo", "ferreteria_conejo_e2e"))
    args = parser.parse_args()
    connection = pymysql.connect(host=Config.DB_HOST, port=int(Config.DB_PORT), user=Config.DB_USER,
                                 password=Config.DB_PASSWORD, database=args.database, charset="utf8mb4")
    try:
        added = migrate(connection, args.database)
        print(f"[OK] {args.database}: columnas añadidas a orders: {', '.join(added) or 'ninguna (ya existían)'}. "
              "Ninguna fila insertada ni modificada; ninguna otra tabla tocada.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
