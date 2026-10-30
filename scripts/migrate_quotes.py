"""Migración: tablas quotes y quote_lines (Quotes Core v1).

Qué hace:
- CREATE TABLE IF NOT EXISTS quotes / quote_lines (InnoDB, utf8mb4), FKs RESTRICT a
  customers, users y products. Sin columnas de stock ni enlace a stock_movements: una
  cotización nunca mueve inventario.
- NO modifica products, stock_movements, customers, sales ni ninguna otra tabla. NO inserta filas.

Requiere que exista customers (scripts/migrate_customers.py). Idempotente.
    python scripts/migrate_quotes.py --database ferreteria_conejo_e2e
    python scripts/migrate_quotes.py --database ferreteria_conejo
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pymysql

from app.config import Config

DDL = {
    "quotes": """CREATE TABLE IF NOT EXISTS quotes (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    quote_number VARCHAR(20) NULL,
    customer_id INT NOT NULL,
    seller_id INT NOT NULL,
    quoted_at DATETIME NOT NULL,
    validity_days INT NOT NULL,
    valid_until DATE NOT NULL,
    subtotal DECIMAL(12,2) NOT NULL DEFAULT 0,
    discount_percent DECIMAL(5,2) NOT NULL DEFAULT 0,
    discount_total DECIMAL(12,2) NOT NULL DEFAULT 0,
    total DECIMAL(12,2) NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL,
    notes VARCHAR(500) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_quotes_quote_number (quote_number),
    KEY ix_quotes_quoted_at (quoted_at),
    KEY ix_quotes_valid_until (valid_until),
    KEY ix_quotes_customer_id (customer_id),
    KEY ix_quotes_seller_id (seller_id),
    KEY ix_quotes_status (status),
    CONSTRAINT fk_quotes_customer FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE RESTRICT,
    CONSTRAINT fk_quotes_seller FOREIGN KEY (seller_id) REFERENCES users (id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "quote_lines": """CREATE TABLE IF NOT EXISTS quote_lines (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    quote_id INT NOT NULL,
    product_id INT NOT NULL,
    product_code VARCHAR(50) NOT NULL,
    product_name VARCHAR(150) NOT NULL,
    quantity INT NOT NULL,
    unit_price DECIMAL(12,2) NOT NULL,
    line_subtotal DECIMAL(12,2) NOT NULL,
    created_at DATETIME NOT NULL,
    KEY ix_quote_lines_quote_id (quote_id),
    KEY ix_quote_lines_product_id (product_id),
    CONSTRAINT fk_quote_lines_quote FOREIGN KEY (quote_id) REFERENCES quotes (id) ON DELETE RESTRICT,
    CONSTRAINT fk_quote_lines_product FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE RESTRICT
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
        if not table_exists(cursor, database, "customers"):
            raise RuntimeError("Falta la tabla customers: ejecute primero scripts/migrate_customers.py.")
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
