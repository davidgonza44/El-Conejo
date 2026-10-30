"""Migración: foto de perfil de usuarios.

Qué hace:
- Agrega la columna users.profile_photo_filename VARCHAR(255) NULL
  (guarda SOLO el nombre del archivo dentro de uploads/users/; nunca base64).
- NO modifica ni borra ninguna otra columna, tabla ni dato existente.

Idempotente: si la columna ya existe, no hace nada.

Uso:
    python scripts/migrate_profile_photo.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pymysql

from app.config import Config

DDL_ADD_COLUMN = (
    "ALTER TABLE users "
    "ADD COLUMN profile_photo_filename VARCHAR(255) NULL "
    "AFTER email_verified"
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
            if _column_exists(cursor, "users", "profile_photo_filename"):
                print("[OK] users.profile_photo_filename ya existe: no se hace nada.")
            else:
                cursor.execute(DDL_ADD_COLUMN)
                print("[OK] Columna users.profile_photo_filename agregada (VARCHAR(255) NULL).")
        connection.commit()
        print("[OK] Migracion completada. Ninguna otra tabla ni columna fue modificada.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
