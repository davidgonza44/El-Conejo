"""Migración: estado y color persistidos de categorías.

Qué hace:
- Agrega categories.is_active TINYINT(1) NOT NULL DEFAULT 1
  (toda categoría existente queda activa).
- Agrega categories.color VARCHAR(20) NOT NULL DEFAULT 'blue'
  (toda categoría existente recibe el color por defecto de la paleta aprobada).
- NO modifica ni borra ninguna otra columna, tabla, dato ni producto.

Idempotente: cada columna se agrega solo si no existe.

Uso:
    python scripts/migrate_category_status_color.py
La BD objetivo es Config.DB_NAME (.env); con PYTHON_DOTENV_DISABLED=1 y
DB_NAME=ferreteria_conejo_e2e se aplica a la BD desechable de E2E.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pymysql

from app.config import Config

COLUMNS = (
    ("is_active", "ALTER TABLE categories ADD COLUMN is_active TINYINT(1) NOT NULL DEFAULT 1 AFTER description"),
    ("color", "ALTER TABLE categories ADD COLUMN color VARCHAR(20) NOT NULL DEFAULT 'blue' AFTER is_active"),
)


def _connect():
    return pymysql.connect(
        host=Config.DB_HOST,
        port=int(Config.DB_PORT),
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        database=Config.DB_NAME,
    )


def _column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (Config.DB_NAME, table, column),
    )
    return cursor.fetchone()[0] > 0


def main() -> None:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            for column, ddl in COLUMNS:
                if _column_exists(cursor, "categories", column):
                    print(f"[OK] categories.{column} ya existe: no se hace nada.")
                else:
                    cursor.execute(ddl)
                    print(f"[OK] Columna categories.{column} agregada.")
            cursor.execute("SELECT COUNT(*), SUM(is_active = 1), SUM(color = 'blue') FROM categories")
            total, active, blue = cursor.fetchone()
            print(f"[OK] BD {Config.DB_NAME}: {total} categorías, {active} activas, {blue} con color 'blue'.")
        connection.commit()
        print("[OK] Migracion completada. Ninguna otra tabla ni columna fue modificada.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
