"""Add Category.icon and initialize legacy visuals ONCE, with a durable marker.

Run manually with the application stopped (MySQL DDL commits implicitly):
    python scripts/migrate_category_icon.py --database ferreteria_conejo

Only exact known names still blue + null/empty/tag are eligible. A committed
schema_data_migrations marker prevents ANY later reseeding, including categories
customized back to blue/tag or created after initialization. Seed and marker are
one InnoDB transaction; a failed seed rolls back and can be retried. The advisory
lock serializes concurrent migration runners. No products are read or changed.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pymysql

from app.config import Config

SEED_KEY = "category_visuals_v1"
INITIAL_VISUALS = (
    ("Construcción", "orange", "hard-hat"),
    ("Electricidad", "yellow", "zap"),
    ("Ferretería en general", "blue", "tag"),
    ("Herramientas", "green", "wrench"),
    ("Pinturas", "purple", "paintbrush"),
    ("Plomería y grifería", "teal", "droplet"),
)


def column_exists(cursor, database, column):
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='categories' AND COLUMN_NAME=%s",
        (database, column),
    )
    return cursor.fetchone()[0] > 0


def initialize_visuals(cursor):
    cursor.execute("SELECT 1 FROM schema_data_migrations WHERE migration_key=%s", (SEED_KEY,))
    if cursor.fetchone():
        return False
    for name, color, icon in INITIAL_VISUALS:
        cursor.execute(
            "UPDATE categories SET color=%s, icon=%s "
            "WHERE BINARY name=BINARY %s AND color='blue' "
            "AND (icon IS NULL OR icon='' OR icon='tag')",
            (color, icon, name),
        )
    cursor.execute("INSERT INTO schema_data_migrations (migration_key) VALUES (%s)", (SEED_KEY,))
    return True


def migrate(connection, database):
    lock = database + ":category_visuals_v1"
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE()")
        if cursor.fetchone()[0] != database:
            raise RuntimeError("La base conectada no coincide con la base solicitada.")
        cursor.execute("SELECT GET_LOCK(%s, 10)", (lock,))
        if cursor.fetchone()[0] != 1:
            raise RuntimeError("No se pudo obtener el bloqueo de migración.")
        try:
            # Verify prerequisites and transactional seed support before any DDL.
            for column in ("color", "is_active"):
                if not column_exists(cursor, database, column):
                    raise RuntimeError("Ejecute primero migrate_category_status_color.py.")
            cursor.execute("SELECT ENGINE FROM information_schema.TABLES "
                           "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='categories'", (database,))
            if cursor.fetchone()[0].lower() != "innodb":
                raise RuntimeError("La inicialización requiere categories en InnoDB.")
            if not column_exists(cursor, database, "icon"):
                cursor.execute("ALTER TABLE categories ADD COLUMN icon VARCHAR(30) NOT NULL DEFAULT 'tag'")
            cursor.execute("SELECT COUNT(*) FROM information_schema.TABLES "
                           "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='schema_data_migrations'", (database,))
            if cursor.fetchone()[0] == 0:
                cursor.execute("CREATE TABLE schema_data_migrations ("
                               "migration_key VARCHAR(100) NOT NULL PRIMARY KEY, "
                               "applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB")
            cursor.execute("SELECT ENGINE FROM information_schema.TABLES "
                           "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='schema_data_migrations'", (database,))
            if cursor.fetchone()[0].lower() != "innodb":
                raise RuntimeError("El registro de migraciones requiere InnoDB.")
            connection.begin()
            initialized = initialize_visuals(cursor)
            connection.commit()
            return initialized
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.execute("SELECT RELEASE_LOCK(%s)", (lock,))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, choices=("ferreteria_conejo", "ferreteria_conejo_e2e"))
    args = parser.parse_args()
    connection = pymysql.connect(host=Config.DB_HOST, port=int(Config.DB_PORT), user=Config.DB_USER,
                                 password=Config.DB_PASSWORD, database=args.database, charset="utf8mb4")
    try:
        initialized = migrate(connection, args.database)
        print("PASS: icon disponible; " + ("inicialización aplicada." if initialized else "inicialización ya aplicada; sin cambios."))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
