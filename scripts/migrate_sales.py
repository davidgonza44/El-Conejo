"""Migración: tablas sales y sale_lines (Sales Core v1).

Qué hace:
- CREATE TABLE IF NOT EXISTS sales / sale_lines (InnoDB, utf8mb4), FKs RESTRICT a
  customers, users, products y stock_movements (sale_lines.stock_movement_id UNIQUE:
  cada renglón enlaza exactamente una salida de inventario).
- NO modifica products, stock_movements, customers ni ninguna otra tabla. NO inserta filas.

Requiere que exista customers (scripts/migrate_customers.py). Idempotente.
    python scripts/migrate_sales.py --database ferreteria_conejo_e2e
    python scripts/migrate_sales.py --database ferreteria_conejo
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pymysql

from app.config import Config

DDL = {
    "sales": """CREATE TABLE IF NOT EXISTS sales (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    sale_number VARCHAR(20) NULL,
    customer_id INT NOT NULL,
    seller_id INT NOT NULL,
    sold_at DATETIME NOT NULL,
    payment_method VARCHAR(20) NOT NULL,
    payment_condition VARCHAR(20) NOT NULL,
    subtotal DECIMAL(12,2) NOT NULL DEFAULT 0,
    discount_total DECIMAL(12,2) NOT NULL DEFAULT 0,
    total DECIMAL(12,2) NOT NULL DEFAULT 0,
    cash_received DECIMAL(12,2) NULL,
    change_due DECIMAL(12,2) NULL,
    status VARCHAR(20) NOT NULL,
    idempotency_key VARCHAR(64) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_sales_sale_number (sale_number),
    UNIQUE KEY uq_sales_idempotency_key (idempotency_key),
    KEY ix_sales_sold_at (sold_at),
    KEY ix_sales_customer_id (customer_id),
    KEY ix_sales_seller_id (seller_id),
    KEY ix_sales_status (status),
    CONSTRAINT fk_sales_customer FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE RESTRICT,
    CONSTRAINT fk_sales_seller FOREIGN KEY (seller_id) REFERENCES users (id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "sale_lines": """CREATE TABLE IF NOT EXISTS sale_lines (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    sale_id INT NOT NULL,
    product_id INT NOT NULL,
    stock_movement_id INT NOT NULL,
    product_code VARCHAR(50) NOT NULL,
    product_name VARCHAR(150) NOT NULL,
    quantity INT NOT NULL,
    unit_price DECIMAL(12,2) NOT NULL,
    discount_percent DECIMAL(5,2) NOT NULL DEFAULT 0,
    discount_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    line_subtotal DECIMAL(12,2) NOT NULL,
    line_total DECIMAL(12,2) NOT NULL,
    created_at DATETIME NOT NULL,
    UNIQUE KEY uq_sale_lines_stock_movement (stock_movement_id),
    KEY ix_sale_lines_sale_id (sale_id),
    KEY ix_sale_lines_product_id (product_id),
    CONSTRAINT fk_sale_lines_sale FOREIGN KEY (sale_id) REFERENCES sales (id) ON DELETE RESTRICT,
    CONSTRAINT fk_sale_lines_product FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE RESTRICT,
    CONSTRAINT fk_sale_lines_movement FOREIGN KEY (stock_movement_id) REFERENCES stock_movements (id) ON DELETE RESTRICT
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
