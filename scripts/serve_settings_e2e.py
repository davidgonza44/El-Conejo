"""Disposable MariaDB harness for Settings Core. Hard-coded to ferreteria_conejo_e2e.

Requires the full Settings schema already migrated in the E2E database
(python scripts/migrate_settings_full.py --database ferreteria_conejo_e2e).
Seeds one admin operator, nine extra users (long names/emails for overflow checks) and one
low-stock product so the shell bell has real data. Backups are written to .tmp/settings-e2e/backups
(never instance/). Self-cleaning: leftovers from previous runs are removed at startup and the
current run can be removed with POST /__settings_e2e__/cleanup. Never touches ferreteria_conejo.

Run with python -B scripts/serve_settings_e2e.py; stop after validation.
"""
import os
import secrets
import sys
import threading
from pathlib import Path

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask import jsonify
from flask_login import login_user
from sqlalchemy import text
from app import create_app
from app.extensions import db
from app.models import Category, User
from app.services import product_service

PASSWORD = 'DisposableSettingsOnly123!'
BACKUP_DIR = ROOT / '.tmp' / 'settings-e2e' / 'backups'
LONG_NAME = 'María Alejandra de la Concepción Rodríguez Fernández de Villalobos y Echeverría'
E2E_USERS = "(SELECT id FROM users WHERE username LIKE 'settings_e2e_%')"
# Orden: hijos sin CASCADE primero; el resto cae en cascada al borrar usuarios (ver migrate_settings_full.py).
CLEANUP_SQL = [
    f"DELETE FROM restore_runs WHERE performed_by_user_id IN {E2E_USERS}",
    f"DELETE FROM backups WHERE created_by_user_id IN {E2E_USERS}",
    f"DELETE FROM app_settings WHERE updated_by_user_id IN {E2E_USERS}",
    f"DELETE FROM login_events WHERE identifier LIKE 'settings_e2e_%' OR identifier LIKE 'settings_%@example.invalid' OR user_id IN {E2E_USERS}",
    "DELETE FROM roles WHERE is_system = 0 AND (name LIKE 'E2E %' OR name LIKE 'Copia de %')",
    "DELETE FROM integration_connections WHERE account_email LIKE '%@example.invalid'",
    "DELETE FROM stock_movements WHERE product_id IN (SELECT id FROM products WHERE code LIKE 'E2E-SET-%')",
    "DELETE FROM products WHERE code LIKE 'E2E-SET-%'",
    "DELETE FROM categories WHERE name LIKE 'Configuración E2E %'",
    "DELETE FROM users WHERE username LIKE 'settings_e2e_%'",
]


class E2EConfig:
    SECRET_KEY = secrets.token_urlsafe(32)
    DEBUG = True
    APP_ENV = 'testing'
    APP_VERSION = '0.1.0'
    MAIL_ENABLED = False
    GOOGLE_CLIENT_ID = None
    GOOGLE_CLIENT_SECRET = None
    GOOGLE_REDIRECT_URI = None
    MYSQLDUMP_BIN = os.environ.get('MYSQLDUMP_BIN', r'C:\xampp\mysql\bin\mysqldump.exe')
    MYSQL_BIN = os.environ.get('MYSQL_BIN', r'C:\xampp\mysql\bin\mysql.exe')
    NOTIFICATIONS_DRY_RUN = True  # proveedores externos simulados: jamás correo/WhatsApp/SMS reales
    GOOGLE_DRIVE_CLIENT_ID = None
    GOOGLE_DRIVE_CLIENT_SECRET = None
    GOOGLE_DRIVE_REDIRECT_URI = None
    BACKUP_TASK_NAME = 'FerreteriaConejo-RespaldoAutomatico-E2E'
    BACKUP_DIR = str(BACKUP_DIR)
    SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://root:@127.0.0.1/ferreteria_conejo_e2e?charset=utf8mb4'
    SQLALCHEMY_TRACK_MODIFICATIONS = False


def cleanup():
    """Delete every synthetic E2E row (all runs) and the E2E dump files. Only runs inside the guarded E2E app."""
    assert db.session.execute(text('SELECT DATABASE()')).scalar() == 'ferreteria_conejo_e2e'
    for statement in CLEANUP_SQL:
        db.session.execute(text(statement))
    db.session.commit()
    for pattern in ('ferreteria_conejo_e2e_*.sql', 'ferreteria_conejo_e2e_*.zip'):
        for dump in BACKUP_DIR.glob(pattern):
            dump.unlink()


def make_server():
    app = create_app(E2EConfig)
    with app.app_context():
        assert db.engine.url.database == 'ferreteria_conejo_e2e'
        engine = db.session.execute(text('SELECT VERSION()')).scalar()
        cleanup()
        run = secrets.token_hex(4)
        admin = User(name='Operador E2E Configuración', username='settings_e2e_' + run,
                     email='settings_' + run + '@example.invalid', role='admin')
        admin.set_password(PASSWORD)
        extra = []
        for index, role in enumerate(['inventario', 'vendedor', 'vendedor', 'inventario', 'vendedor', 'vendedor', 'admin', 'vendedor']):
            user = User(name=f'Usuario E2E {index:02} {role.title()}', username=f'settings_e2e_{run}_{index:02}',
                        email=f'settings_{run}_{index:02}@example.invalid', role=role, is_active=index % 3 != 2)
            user.set_password(PASSWORD)
            extra.append(user)
        long_user = User(name=LONG_NAME, username=f'settings_e2e_{run}_long',
                         email=f'settings_{run}_nombre.extremadamente.largo.para.probar.el.desborde@example.invalid', role='vendedor')
        long_user.set_password(PASSWORD)
        category = Category(name='Configuración E2E ' + run)
        db.session.add_all([admin, *extra, long_user, category])
        db.session.commit()
        with app.test_request_context():
            login_user(admin)
            product = product_service.create_product({
                'code': f'E2E-SET-{run}', 'name': 'Tornillo E2E autorroscante 1" caja 100 unidades',
                'category_id': category.id, 'current_stock': 2, 'minimum_stock': 10,
                'purchase_price': '1.00', 'sale_price': '2.00',
            })
        context = {'database': 'ferreteria_conejo_e2e', 'engine': engine, 'run': run,
                   'username': admin.username, 'password': PASSWORD, 'admin_id': admin.id,
                   'long_user_id': long_user.id, 'low_stock_product_id': product.id, 'backup_dir': str(BACKUP_DIR),
                   'mysqldump_bin': E2EConfig.MYSQLDUMP_BIN}

    @app.get('/__settings_e2e__/context')
    def test_context():
        # Only the dedicated local harness exposes this synthetic metadata.
        return jsonify(context)

    @app.post('/__settings_e2e__/cleanup')
    def test_cleanup():
        cleanup()
        return jsonify({'cleaned': True})

    @app.get('/__settings_e2e__/counts')
    def test_counts():
        """Conteo de filas por tabla: la suite compara antes/después de recorrer ?ref=1 (debe ser idéntico)."""
        tables = ('users', 'roles', 'role_permissions', 'app_settings', 'user_sessions', 'login_events', 'password_history',
                  'user_mfa', 'mfa_recovery_codes', 'trusted_devices', 'notifications', 'notification_deliveries',
                  'backups', 'restore_runs', 'integration_connections')
        return jsonify({t: db.session.execute(text(f'SELECT COUNT(*) FROM {t}')).scalar() for t in tables})

    @app.post('/__settings_e2e__/shutdown')
    def test_shutdown():
        """Apagado ordenado del arnés local (evita matar procesos a ciegas entre corridas)."""
        threading.Timer(0.5, lambda: os._exit(0)).start()
        return jsonify({'shutting_down': True})

    return app


if __name__ == '__main__':
    port = int(os.environ.get('SETTINGS_E2E_PORT', '5026'))
    make_server().run(host='127.0.0.1', port=port, debug=True, use_reloader=False, use_debugger=False)
