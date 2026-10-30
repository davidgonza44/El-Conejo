"""Migración: tabla app_settings (configuración persistida de la aplicación).

Qué hace:
- CREATE TABLE IF NOT EXISTS app_settings (setting_key PK, value_json JSON, updated_at,
  updated_by_user_id FK RESTRICT a users). Guardada con information_schema: correr dos veces
  es seguro.
- NO modifica ninguna otra tabla. NO inserta filas (los valores por defecto viven en código).

Idempotente. Probar primero en la base desechable:
    python scripts/migrate_app_settings.py --database ferreteria_conejo_e2e
    python scripts/migrate_app_settings.py --database ferreteria_conejo
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pymysql

from app.config import Config

DDL = {
    "app_settings": """CREATE TABLE IF NOT EXISTS app_settings (
    setting_key VARCHAR(64) NOT NULL PRIMARY KEY,
    value_json JSON NOT NULL,
    updated_at DATETIME NOT NULL,
    updated_by_user_id INT NULL,
    CONSTRAINT fk_app_settings_user FOREIGN KEY (updated_by_user_id) REFERENCES users (id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
}


def table_exists(cursor, database, table):
    cursor.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                   (database, table))
    return cursor.fetchone()[0] > 0


def migrate(connection, database):
    created = []
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE()")
        if cursor.fetchone()[0] != database:
            raise RuntimeError("La conexión no apunta a la base indicada.")
        if not table_exists(cursor, database, "users"):
            raise RuntimeError("Falta la tabla users: inicialice la base antes de migrar.")
        for table, ddl in DDL.items():
            if not table_exists(cursor, database, table):
                cursor.execute(ddl)
                created.append(table)
        connection.commit()
    return created


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, choices=("ferreteria_conejo", "ferreteria_conejo_e2e"))
    args = parser.parse_args()
    connection = pymysql.connect(host=Config.DB_HOST, port=int(Config.DB_PORT), user=Config.DB_USER,
                                 password=Config.DB_PASSWORD, database=args.database, charset="utf8mb4")
    try:
        created = migrate(connection, args.database)
        print(f"[OK] {args.database}: tablas creadas: {', '.join(created) or 'ninguna (ya existían)'}. "
              "Ninguna fila insertada; ninguna otra tabla modificada.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
