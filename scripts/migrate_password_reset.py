"""Migración del módulo de recuperación de contraseña.

Qué hace:
- Verifica que users.id sea INT (el mismo tipo que usará user_id).
- Crea la tabla password_reset_tokens si no existe (CREATE TABLE IF NOT EXISTS).
- Crea los índices necesarios si faltan.
- NO modifica ni borra ninguna otra tabla ni ningún dato existente.

Idempotente: puede ejecutarse varias veces sin efectos secundarios.

Uso:
    python scripts/migrate_password_reset.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pymysql

from app.config import Config

DDL_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id INT NOT NULL AUTO_INCREMENT,
    user_id INT NULL,
    email VARCHAR(120) NOT NULL,
    token_hash VARCHAR(255) NOT NULL,
    expires_at DATETIME NOT NULL,
    used_at DATETIME NULL,
    created_at DATETIME NOT NULL,
    request_ip VARCHAR(45) NULL,
    user_agent VARCHAR(255) NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_password_reset_tokens_user_id
        FOREIGN KEY (user_id) REFERENCES users (id),
    INDEX ix_password_reset_tokens_user_id (user_id),
    INDEX ix_password_reset_tokens_email (email),
    INDEX ix_password_reset_tokens_token_hash (token_hash),
    INDEX ix_password_reset_tokens_expires_at (expires_at),
    INDEX ix_password_reset_tokens_used_at (used_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

INDEXES = {
    "ix_password_reset_tokens_user_id": "user_id",
    "ix_password_reset_tokens_email": "email",
    "ix_password_reset_tokens_token_hash": "token_hash",
    "ix_password_reset_tokens_expires_at": "expires_at",
    "ix_password_reset_tokens_used_at": "used_at",
}


def _connect():
    return pymysql.connect(
        host=Config.DB_HOST,
        port=int(Config.DB_PORT),
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        database=Config.DB_NAME,
    )


def _users_id_type(cursor) -> str:
    cursor.execute(
        "SELECT DATA_TYPE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'users' AND COLUMN_NAME = 'id'",
        (Config.DB_NAME,),
    )
    row = cursor.fetchone()
    if row is None:
        raise SystemExit(
            "[ERROR] No existe la tabla users. Ejecute primero scripts/init_db.py."
        )
    return str(row[0]).lower()


def _table_exists(cursor, table: str) -> bool:
    cursor.execute("SHOW TABLES LIKE %s", (table,))
    return cursor.fetchone() is not None


def _index_exists(cursor, table: str, index: str) -> bool:
    cursor.execute(f"SHOW INDEX FROM `{table}` WHERE Key_name = %s", (index,))
    return cursor.fetchone() is not None


def main() -> None:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            id_type = _users_id_type(cursor)
            if id_type != "int":
                raise SystemExit(
                    f"[ERROR] users.id es de tipo '{id_type}', se esperaba INT. "
                    "Ajuste el DDL de user_id antes de migrar."
                )
            print("[OK] users.id es INT: password_reset_tokens.user_id usa el mismo tipo.")

            if _table_exists(cursor, "password_reset_tokens"):
                print("[OK] La tabla password_reset_tokens ya existe: no se recrea.")
            else:
                cursor.execute(DDL_CREATE_TABLE)
                print("[OK] Tabla password_reset_tokens creada.")

            for index, column in INDEXES.items():
                if _index_exists(cursor, "password_reset_tokens", index):
                    print(f"[OK] Indice {index} ya existe.")
                else:
                    cursor.execute(
                        f"ALTER TABLE password_reset_tokens ADD INDEX {index} ({column})"
                    )
                    print(f"[OK] Indice {index} creado.")

        connection.commit()
        print(
            "[OK] Migracion de password_reset_tokens completada. "
            "Ninguna otra tabla fue modificada."
        )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
