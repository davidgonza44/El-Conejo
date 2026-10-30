"""Migración: tabla inventory_snapshots (cierre comercial diario por producto).

Qué hace:
- CREATE TABLE IF NOT EXISTS inventory_snapshots (InnoDB), una fila por producto y día,
  UNIQUE(snapshot_date, product_id), FK RESTRICT a products. category_id sin FK a propósito.
- NO modifica otras tablas, NO inserta filas, NO genera historial retroactivo.

Idempotente. La base objetivo es obligatoria y explícita:
    python scripts/migrate_inventory_snapshots.py --database ferreteria_conejo_e2e
    python scripts/migrate_inventory_snapshots.py --database ferreteria_conejo
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pymysql

from app.config import Config

TABLE = "inventory_snapshots"
DDL = f"""CREATE TABLE IF NOT EXISTS {TABLE} (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    product_id INT NOT NULL,
    stock INT NOT NULL,
    minimum_stock INT NOT NULL,
    product_is_active TINYINT(1) NOT NULL,
    category_id INT NOT NULL,
    category_is_active TINYINT(1) NOT NULL,
    created_at DATETIME NOT NULL,
    UNIQUE KEY uq_inventory_snapshots_date_product (snapshot_date, product_id),
    CONSTRAINT fk_inventory_snapshots_product FOREIGN KEY (product_id) REFERENCES products (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""


def table_exists(cursor, database):
    cursor.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                   (database, TABLE))
    return cursor.fetchone()[0] > 0


def migrate(connection, database):
    """Crea la tabla si falta. Devuelve True si la creó, False si ya existía."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE()")
        if cursor.fetchone()[0] != database:
            raise RuntimeError("La conexión no apunta a la base indicada.")
        if table_exists(cursor, database):
            return False
        cursor.execute(DDL)
        connection.commit()
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, choices=("ferreteria_conejo", "ferreteria_conejo_e2e"))
    args = parser.parse_args()
    connection = pymysql.connect(host=Config.DB_HOST, port=int(Config.DB_PORT), user=Config.DB_USER,
                                 password=Config.DB_PASSWORD, database=args.database, charset="utf8mb4")
    try:
        created = migrate(connection, args.database)
        print(f"[OK] {args.database}: tabla {TABLE} {'creada' if created else 'ya existía; no se hizo nada'}."
              " Ninguna fila insertada; ninguna otra tabla modificada.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
