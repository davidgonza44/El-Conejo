"""Migración aditiva e idempotente para la Configuración completa (roles, seguridad, notificaciones,
respaldos e integraciones).

Qué hace (todo guardado con information_schema; correr dos veces es seguro):
- Tablas nuevas: roles, permissions, role_permissions, password_history, user_sessions, login_events,
  user_mfa, mfa_recovery_codes, trusted_devices, notifications, notification_deliveries, restore_runs,
  integration_connections.
- Columnas nuevas: users.last_login_at, users.password_changed_at, backups.remote_file_id,
  backups.remote_status, backups.file_count, backups.source.
- Siembra (INSERT IGNORE) el catálogo de permisos y los roles del sistema con los permisos de
  app/utils/permissions.py. No borra asignaciones. products:view_cost queda solo en admin e inventario.
- NO borra ni modifica filas existentes. NO crea usuarios. NO toca datos de negocio.

Probar primero en la base desechable:
    python scripts/migrate_settings_full.py --database ferreteria_conejo_e2e
    python scripts/migrate_settings_full.py --database ferreteria_conejo
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pymysql

from app.config import Config
from app.models.user import ROLES
from app.utils.permissions import ALL_PERMISSIONS, ROLE_PERMISSIONS, permission_label

ALLOWED_DATABASES = ("ferreteria_conejo", "ferreteria_conejo_e2e", "ferreteria_conejo_restore_e2e")
ROLE_SEED = {
    "admin": ("Administrador", "Acceso total al sistema, configuración y respaldos."),
    "inventario": ("Inventario", "Gestión de productos, categorías, movimientos y reportes."),
    "vendedor": ("Vendedor", "Ventas, clientes, cotizaciones, pedidos y notas de entrega."),
    "soporte_ti": ("Soporte TI", "Configuración técnica, sesiones propias y respaldos, sin operación comercial."),
}

_ENGINE = ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"

TABLES = {
    "permissions": """CREATE TABLE IF NOT EXISTS permissions (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    code VARCHAR(64) NOT NULL,
    module VARCHAR(40) NOT NULL,
    label VARCHAR(120) NOT NULL,
    UNIQUE KEY uq_permissions_code (code)
""" + _ENGINE,
    "roles": """CREATE TABLE IF NOT EXISTS roles (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    slug VARCHAR(20) NOT NULL,
    name VARCHAR(60) NOT NULL,
    description VARCHAR(255) NULL,
    is_system TINYINT(1) NOT NULL DEFAULT 0,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_roles_slug (slug),
    UNIQUE KEY uq_roles_name (name)
""" + _ENGINE,
    "role_permissions": """CREATE TABLE IF NOT EXISTS role_permissions (
    role_id INT NOT NULL,
    permission_id INT NOT NULL,
    PRIMARY KEY (role_id, permission_id),
    CONSTRAINT fk_role_permissions_role FOREIGN KEY (role_id) REFERENCES roles (id) ON DELETE CASCADE,
    CONSTRAINT fk_role_permissions_permission FOREIGN KEY (permission_id) REFERENCES permissions (id) ON DELETE CASCADE
""" + _ENGINE,
    "password_history": """CREATE TABLE IF NOT EXISTS password_history (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at DATETIME NOT NULL,
    KEY ix_password_history_user (user_id),
    CONSTRAINT fk_password_history_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
""" + _ENGINE,
    "user_sessions": """CREATE TABLE IF NOT EXISTS user_sessions (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    ip_address VARCHAR(45) NULL,
    user_agent VARCHAR(255) NULL,
    device_label VARCHAR(80) NULL,
    provider VARCHAR(20) NOT NULL,
    created_at DATETIME NOT NULL,
    last_seen_at DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    revoked_at DATETIME NULL,
    UNIQUE KEY uq_user_sessions_token (token_hash),
    KEY ix_user_sessions_user (user_id),
    CONSTRAINT fk_user_sessions_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
""" + _ENGINE,
    "login_events": """CREATE TABLE IF NOT EXISTS login_events (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id INT NULL,
    identifier VARCHAR(120) NOT NULL,
    provider VARCHAR(20) NOT NULL,
    success TINYINT(1) NOT NULL,
    reason VARCHAR(40) NULL,
    ip_address VARCHAR(45) NULL,
    user_agent VARCHAR(255) NULL,
    device_label VARCHAR(80) NULL,
    created_at DATETIME NOT NULL,
    KEY ix_login_events_user (user_id),
    KEY ix_login_events_created (created_at),
    CONSTRAINT fk_login_events_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
""" + _ENGINE,
    "user_mfa": """CREATE TABLE IF NOT EXISTS user_mfa (
    user_id INT NOT NULL PRIMARY KEY,
    secret_encrypted TEXT NULL,
    pending_secret_encrypted TEXT NULL,
    enabled_at DATETIME NULL,
    last_verified_step BIGINT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT fk_user_mfa_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
""" + _ENGINE,
    "mfa_recovery_codes": """CREATE TABLE IF NOT EXISTS mfa_recovery_codes (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    code_hash VARCHAR(64) NOT NULL,
    used_at DATETIME NULL,
    created_at DATETIME NOT NULL,
    UNIQUE KEY uq_mfa_recovery_codes_hash (code_hash),
    KEY ix_mfa_recovery_codes_user (user_id),
    CONSTRAINT fk_mfa_recovery_codes_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
""" + _ENGINE,
    "trusted_devices": """CREATE TABLE IF NOT EXISTS trusted_devices (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    label VARCHAR(80) NOT NULL,
    user_agent VARCHAR(255) NULL,
    ip_address VARCHAR(45) NULL,
    created_at DATETIME NOT NULL,
    last_used_at DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    revoked_at DATETIME NULL,
    UNIQUE KEY uq_trusted_devices_token (token_hash),
    KEY ix_trusted_devices_user (user_id),
    CONSTRAINT fk_trusted_devices_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
""" + _ENGINE,
    "notifications": """CREATE TABLE IF NOT EXISTS notifications (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    event VARCHAR(20) NOT NULL,
    severity VARCHAR(10) NOT NULL,
    title VARCHAR(160) NOT NULL,
    message VARCHAR(500) NOT NULL,
    entity_type VARCHAR(30) NULL,
    entity_id INT NULL,
    href VARCHAR(255) NULL,
    dedupe_key VARCHAR(120) NULL,
    read_at DATETIME NULL,
    created_at DATETIME NOT NULL,
    KEY ix_notifications_user (user_id),
    KEY ix_notifications_event (event),
    KEY ix_notifications_dedupe (dedupe_key),
    KEY ix_notifications_created (created_at),
    CONSTRAINT fk_notifications_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
""" + _ENGINE,
    "notification_deliveries": """CREATE TABLE IF NOT EXISTS notification_deliveries (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    notification_id INT NOT NULL,
    channel VARCHAR(10) NOT NULL,
    status VARCHAR(10) NOT NULL,
    detail VARCHAR(255) NULL,
    created_at DATETIME NOT NULL,
    KEY ix_notification_deliveries_notification (notification_id),
    CONSTRAINT fk_notification_deliveries_notification FOREIGN KEY (notification_id)
        REFERENCES notifications (id) ON DELETE CASCADE
""" + _ENGINE,
    "restore_runs": """CREATE TABLE IF NOT EXISTS restore_runs (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    backup_id INT NULL,
    backup_filename VARCHAR(160) NOT NULL,
    kind VARCHAR(20) NOT NULL,
    target_database VARCHAR(64) NOT NULL,
    safety_backup_id INT NULL,
    status VARCHAR(20) NOT NULL,
    detail VARCHAR(255) NULL,
    performed_by_user_id INT NOT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT fk_restore_runs_backup FOREIGN KEY (backup_id) REFERENCES backups (id) ON DELETE SET NULL,
    CONSTRAINT fk_restore_runs_safety FOREIGN KEY (safety_backup_id) REFERENCES backups (id) ON DELETE SET NULL,
    CONSTRAINT fk_restore_runs_user FOREIGN KEY (performed_by_user_id) REFERENCES users (id) ON DELETE RESTRICT
""" + _ENGINE,
    "integration_connections": """CREATE TABLE IF NOT EXISTS integration_connections (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    provider VARCHAR(30) NOT NULL,
    account_email VARCHAR(120) NULL,
    token_encrypted TEXT NOT NULL,
    scope VARCHAR(255) NULL,
    remote_folder_id VARCHAR(120) NULL,
    status VARCHAR(20) NOT NULL,
    status_detail VARCHAR(255) NULL,
    connected_by_user_id INT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_integration_connections_provider (provider),
    CONSTRAINT fk_integration_connections_user FOREIGN KEY (connected_by_user_id)
        REFERENCES users (id) ON DELETE SET NULL
""" + _ENGINE,
}

COLUMNS = (
    ("users", "last_login_at", "ALTER TABLE users ADD COLUMN last_login_at DATETIME NULL"),
    ("users", "password_changed_at", "ALTER TABLE users ADD COLUMN password_changed_at DATETIME NULL"),
    ("backups", "remote_file_id", "ALTER TABLE backups ADD COLUMN remote_file_id VARCHAR(120) NULL"),
    ("backups", "remote_status", "ALTER TABLE backups ADD COLUMN remote_status VARCHAR(20) NULL"),
    ("backups", "file_count", "ALTER TABLE backups ADD COLUMN file_count INT NULL"),
    ("backups", "source", "ALTER TABLE backups ADD COLUMN source VARCHAR(20) NOT NULL DEFAULT 'manual'"),
)


def table_exists(cursor, database, table):
    cursor.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                   (database, table))
    return cursor.fetchone()[0] > 0


def column_exists(cursor, database, table, column):
    cursor.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
                   "AND COLUMN_NAME=%s", (database, table, column))
    return cursor.fetchone()[0] > 0


VIEW_COST_CODE = "products:view_cost"
_ASSIGN_VIEW_COST = (
    "INSERT IGNORE INTO role_permissions (role_id, permission_id) "
    "SELECT r.id, p.id FROM roles r, permissions p WHERE r.slug=%s AND p.code=%s"
)


def view_cost_statements():
    """Tres INSERT IGNORE: el permiso y su asignación a admin e inventario."""
    label = permission_label(VIEW_COST_CODE)
    return (
        ("permission",
         "INSERT IGNORE INTO permissions (code, module, label) VALUES (%s, %s, %s)",
         (VIEW_COST_CODE, "products", label)),
        ("admin", _ASSIGN_VIEW_COST, ("admin", VIEW_COST_CODE)),
        ("inventario", _ASSIGN_VIEW_COST, ("inventario", VIEW_COST_CODE)),
    )


def grant_view_cost(cursor):
    """INSERT IGNORE de products:view_cost solo para admin e inventario.

    No crea el permiso en vendedor, soporte_ti ni roles personalizados.
    Correrlo dos veces no duplica filas.
    """
    for _name, sql, params in view_cost_statements():
        cursor.execute(sql, params)


def seed_rbac(cursor):
    """Catálogo de permisos + roles del sistema con sus permisos actuales. INSERT IGNORE: idempotente."""
    for code in sorted(ALL_PERMISSIONS):
        cursor.execute("INSERT IGNORE INTO permissions (code, module, label) VALUES (%s, %s, %s)",
                       (code, code.split(":")[0], permission_label(code)))
    for slug in ROLES:
        name, description = ROLE_SEED[slug]
        cursor.execute("INSERT IGNORE INTO roles (slug, name, description, is_system, is_active, created_at, "
                       "updated_at) VALUES (%s, %s, %s, 1, 1, UTC_TIMESTAMP(), UTC_TIMESTAMP())",
                       (slug, name, description))
        for code in sorted(ROLE_PERMISSIONS[slug]):
            cursor.execute("INSERT IGNORE INTO role_permissions (role_id, permission_id) "
                           "SELECT r.id, p.id FROM roles r, permissions p WHERE r.slug=%s AND p.code=%s",
                           (slug, code))
    grant_view_cost(cursor)


def migrate(connection, database):
    created, added = [], []
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE()")
        if cursor.fetchone()[0] != database:
            raise RuntimeError("La conexión no apunta a la base indicada.")
        for required in ("users", "backups"):
            if not table_exists(cursor, database, required):
                raise RuntimeError(f"Falta la tabla {required}: ejecute las migraciones previas antes de esta.")
        for table, ddl in TABLES.items():
            if not table_exists(cursor, database, table):
                cursor.execute(ddl)
                created.append(table)
        for table, column, ddl in COLUMNS:
            if not column_exists(cursor, database, table, column):
                cursor.execute(ddl)
                added.append(f"{table}.{column}")
        seed_rbac(cursor)
        connection.commit()
    return created, added


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, choices=ALLOWED_DATABASES)
    args = parser.parse_args()
    connection = pymysql.connect(host=Config.DB_HOST, port=int(Config.DB_PORT), user=Config.DB_USER,
                                 password=Config.DB_PASSWORD, database=args.database, charset="utf8mb4")
    try:
        created, added = migrate(connection, args.database)
        print(f"[OK] {args.database}: tablas creadas: {', '.join(created) or 'ninguna (ya existían)'}; "
              f"columnas agregadas: {', '.join(added) or 'ninguna'}. Catálogo RBAC sembrado (INSERT IGNORE). "
              "Ninguna fila existente modificada ni eliminada.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
