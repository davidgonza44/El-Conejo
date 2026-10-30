"""Migración: tabla backups (metadatos de respaldos manuales de la base de datos).

Qué hace:
- CREATE TABLE IF NOT EXISTS backups (filename UNIQUE, sha256, byte_size, database_name, kind,
  status, created_by_user_id FK RESTRICT a users, created_at). Guardada con information_schema:
  correr dos veces es seguro.
- NO modifica ninguna otra tabla. NO inserta filas. NO crea ni restaura respaldos.

Idempotente. Probar primero en la base desechable:
    python scripts/migrate_backups.py --database ferreteria_conejo_e2e
    python scripts/migrate_backups.py --database ferreteria_conejo
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pymysql

from app.config import Config

DDL = {
    "backups": """CREATE TABLE IF NOT EXISTS backups (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    filename VARCHAR(160) NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    byte_size BIGINT NOT NULL,
    database_name VARCHAR(64) NOT NULL,
    kind VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL,
    created_by_user_id INT NOT NULL,
    created_at DATETIME NOT NULL,
    UNIQUE KEY uq_backups_filename (filename),
    KEY ix_backups_created_at (created_at),
    CONSTRAINT fk_backups_user FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE RESTRICT
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
