"""Migración: tabla product_state_histories (historial tipado de minimum_stock e is_active).

Qué hace:
- CREATE TABLE IF NOT EXISTS product_state_histories (InnoDB), una fila por
  transición relevante o baseline inicial, con previous/new tipados.
- FK RESTRICT a products y a users (changed_by nullable = sistema).
- Índice compuesto (product_id, changed_at, id) para reconstrucción estable.
- NO modifica otras tablas, NO inserta filas, NO genera baseline.

Idempotente. La base objetivo es obligatoria y explícita. Sin --apply solo
muestra el DDL (--dry-run):
    python scripts/migrate_product_state_history.py --database ferreteria_conejo_e2e --dry-run
    python scripts/migrate_product_state_history.py --database ferreteria_conejo_e2e --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pymysql

from app.config import Config

TABLE = "product_state_histories"
DDL = f"""CREATE TABLE IF NOT EXISTS {TABLE} (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    product_id INT NOT NULL,
    previous_minimum_stock INT NOT NULL,
    new_minimum_stock INT NOT NULL,
    previous_is_active TINYINT(1) NOT NULL,
    new_is_active TINYINT(1) NOT NULL,
    event_type VARCHAR(20) NOT NULL,
    changed_at DATETIME NOT NULL,
    changed_by_user_id INT NULL,
    KEY ix_product_state_histories_product_changed (product_id, changed_at, id),
    CONSTRAINT fk_product_state_histories_product FOREIGN KEY (product_id) REFERENCES products (id),
    CONSTRAINT fk_product_state_histories_user FOREIGN KEY (changed_by_user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""

DATABASES = ("ferreteria_conejo", "ferreteria_conejo_e2e", "ferreteria_conejo_tesis")


def table_exists(cursor, database):
    cursor.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                   (database, TABLE))
    return cursor.fetchone()[0] > 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, choices=DATABASES)
    parser.add_argument("--dry-run", action="store_true", help="muestra el DDL sin ejecutar nada")
    parser.add_argument("--apply", action="store_true", help="crea la tabla")
    args = parser.parse_args()
    if args.dry_run and args.apply:
        raise SystemExit("--dry-run y --apply son excluyentes.")
    if not args.dry_run and not args.apply:
        raise SystemExit("Indique --dry-run o --apply.")
    if args.dry_run:
        print(f"[DRY-RUN] {args.database}: se crearía la tabla {TABLE} con:")
        print(DDL + ";")
        print("Ninguna fila insertada; ninguna otra tabla modificada.")
        return
    connection = pymysql.connect(host=Config.DB_HOST, port=int(Config.DB_PORT), user=Config.DB_USER,
                                 password=Config.DB_PASSWORD, database=args.database, charset="utf8mb4")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE()")
            if cursor.fetchone()[0] != args.database:
                raise RuntimeError("La conexión no apunta a la base indicada.")
            if table_exists(cursor, args.database):
                print(f"[OK] {args.database}: tabla {TABLE} ya existía; no se hizo nada.")
                return
            cursor.execute(DDL)
        connection.commit()
        print(f"[OK] {args.database}: tabla {TABLE} creada. Ninguna fila insertada; ninguna otra tabla modificada.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
