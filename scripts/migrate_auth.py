"""Migración del módulo de autenticación (incremento 4, fases 1-2).

- Agrega columnas nuevas a users (username, is_active, email_verified, updated_at)
  y hace password_hash nullable.
- Hace backfill de username y migra el rol 'empleado' a 'inventario'.
- Crea las tablas auth_identities y passwordless_tokens.
- Crea la identidad 'local' de los usuarios existentes con contraseña.

Idempotente: puede ejecutarse varias veces.

Uso:
    python scripts/migrate_auth.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pymysql

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import AuthIdentity, User
from app.models.auth_identity import PROVIDER_LOCAL


def _column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE %s", (column,))
    return cursor.fetchone() is not None


def _index_exists(cursor, table: str, index: str) -> bool:
    cursor.execute(f"SHOW INDEX FROM `{table}` WHERE Key_name = %s", (index,))
    return cursor.fetchone() is not None


def alter_users_table() -> None:
    connection = pymysql.connect(
        host=Config.DB_HOST,
        port=int(Config.DB_PORT),
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        database=Config.DB_NAME,
    )
    try:
        with connection.cursor() as cursor:
            if not _column_exists(cursor, "users", "username"):
                cursor.execute(
                    "ALTER TABLE users ADD COLUMN username VARCHAR(50) NULL AFTER email"
                )
                print("[OK] Columna users.username agregada.")

            cursor.execute("ALTER TABLE users MODIFY password_hash VARCHAR(255) NULL")
            print("[OK] users.password_hash ahora es nullable.")

            if not _column_exists(cursor, "users", "is_active"):
                cursor.execute(
                    "ALTER TABLE users ADD COLUMN is_active TINYINT(1) NOT NULL DEFAULT 1"
                )
                print("[OK] Columna users.is_active agregada.")

            if not _column_exists(cursor, "users", "email_verified"):
                cursor.execute(
                    "ALTER TABLE users ADD COLUMN email_verified TINYINT(1) NOT NULL DEFAULT 0"
                )
                print("[OK] Columna users.email_verified agregada.")

            if not _column_exists(cursor, "users", "updated_at"):
                cursor.execute("ALTER TABLE users ADD COLUMN updated_at DATETIME NULL")
                cursor.execute("UPDATE users SET updated_at = created_at")
                print("[OK] Columna users.updated_at agregada y rellenada.")

            # Backfill de username: parte local del email. Los duplicados se
            # resuelven después en dedupe_usernames_and_constraints().
            cursor.execute(
                "UPDATE users SET username = SUBSTRING_INDEX(email, '@', 1) "
                "WHERE username IS NULL OR username = ''"
            )

            # Migración de roles antiguos.
            cursor.execute("UPDATE users SET role = 'inventario' WHERE role = 'empleado'")

        connection.commit()
    finally:
        connection.close()


def dedupe_usernames_and_constraints() -> None:
    """Resuelve usernames duplicados en Python y aplica NOT NULL + UNIQUE."""
    connection = pymysql.connect(
        host=Config.DB_HOST,
        port=int(Config.DB_PORT),
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        database=Config.DB_NAME,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, username FROM users ORDER BY id"
            )
            seen: set[str] = set()
            for user_id, username in cursor.fetchall():
                candidate = username
                while candidate in seen:
                    candidate = f"{username}{user_id}"
                if candidate != username:
                    cursor.execute(
                        "UPDATE users SET username = %s WHERE id = %s",
                        (candidate, user_id),
                    )
                seen.add(candidate)

            cursor.execute("ALTER TABLE users MODIFY username VARCHAR(50) NOT NULL")
            if not _index_exists(cursor, "users", "ux_users_username"):
                cursor.execute(
                    "ALTER TABLE users ADD UNIQUE INDEX ux_users_username (username)"
                )
                print("[OK] Índice único ux_users_username creado.")

        connection.commit()
    finally:
        connection.close()


def create_new_tables_and_seed_identities() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()
        print("[OK] Tablas auth_identities y passwordless_tokens creadas/verificadas.")

        users_with_password = User.query.filter(User.password_hash.isnot(None)).all()
        created = 0
        for user in users_with_password:
            exists = AuthIdentity.query.filter_by(
                provider=PROVIDER_LOCAL, provider_user_id=str(user.id)
            ).first()
            if exists is None:
                db.session.add(
                    AuthIdentity(
                        user_id=user.id,
                        provider=PROVIDER_LOCAL,
                        provider_user_id=str(user.id),
                        email=user.email,
                    )
                )
                created += 1
        db.session.commit()
        print(f"[OK] Identidades locales creadas: {created}.")


def main() -> None:
    alter_users_table()
    dedupe_usernames_and_constraints()
    create_new_tables_and_seed_identities()
    print("[OK] Migración de autenticación completada.")


if __name__ == "__main__":
    main()
