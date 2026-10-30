"""Migración: tabla customers (directorio único de clientes) + cliente genérico.

Qué hace:
- CREATE TABLE IF NOT EXISTS customers (InnoDB, utf8mb4): UNIQUE tax_id, UNIQUE email.
- Inserta exactamente un cliente "Público en general" (is_walk_in=1) si no existe ninguno.
- NO modifica otras tablas. Idempotente: la segunda pasada no crea otro genérico.

La base objetivo es obligatoria y explícita:
    python scripts/migrate_customers.py --database ferreteria_conejo_e2e
    python scripts/migrate_customers.py --database ferreteria_conejo
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pymysql

from app.config import Config

TABLE = "customers"
WALK_IN_NAME = "Público en general"
DDL = f"""CREATE TABLE IF NOT EXISTS {TABLE} (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    tax_id VARCHAR(20) NULL,
    phone VARCHAR(30) NULL,
    email VARCHAR(120) NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    is_walk_in TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_customers_tax_id (tax_id),
    UNIQUE KEY uq_customers_email (email),
    KEY ix_customers_name (name),
    KEY ix_customers_is_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""


def table_exists(cursor, database):
    cursor.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                   (database, TABLE))
    return cursor.fetchone()[0] > 0


def migrate(connection, database):
    """Devuelve (tabla_creada, generico_insertado)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE()")
        if cursor.fetchone()[0] != database:
            raise RuntimeError("La conexión no apunta a la base indicada.")
        created = not table_exists(cursor, database)
        if created:
            cursor.execute(DDL)
        cursor.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE is_walk_in = 1")
        seeded = cursor.fetchone()[0] == 0
        if seeded:
            cursor.execute(
                f"INSERT INTO {TABLE} (name, tax_id, phone, email, is_active, is_walk_in, created_at, updated_at) "
                "VALUES (%s, NULL, NULL, NULL, 1, 1, UTC_TIMESTAMP(), UTC_TIMESTAMP())",
                (WALK_IN_NAME,),
            )
        connection.commit()
        return created, seeded


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, choices=("ferreteria_conejo", "ferreteria_conejo_e2e"))
    args = parser.parse_args()
    connection = pymysql.connect(host=Config.DB_HOST, port=int(Config.DB_PORT), user=Config.DB_USER,
                                 password=Config.DB_PASSWORD, database=args.database, charset="utf8mb4")
    try:
        created, seeded = migrate(connection, args.database)
        print(f"[OK] {args.database}: tabla {TABLE} {'creada' if created else 'ya existía'}; "
              f"cliente genérico {'insertado' if seeded else 'ya existía; no se duplicó'}. "
              "Ninguna otra tabla modificada.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
