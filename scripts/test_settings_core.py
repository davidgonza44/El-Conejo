#!/usr/bin/env python3
"""Suite aislada de Settings Core: SQLite temporal, sin MySQL ni ferreteria_conejo.

Cubre: página en vivo por pestaña (shell Pro, sin fixture), /api/settings (permisos, claves
soportadas, validación), campana que respeta alerts.low_stock_enabled, usuarios (último admin,
registro público sin escalada, sesión inactiva rechazada), cambio de contraseña, política de
longitud mínima en alta/cambio/recuperación, respaldos (403 no admin, 503 sin mysqldump/MySQL,
sin ruta de restore). El volcado real con mysqldump se prueba en scripts/test_settings_e2e.py.

Uso: python scripts/test_settings_core.py
"""
from __future__ import annotations

import os
import re
import secrets
import sqlite3
import sys
import tempfile
from pathlib import Path

from sqlalchemy import BigInteger
from sqlalchemy.engine import Engine
from sqlalchemy.event import listens_for
from sqlalchemy.ext.compiler import compiles

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import PasswordResetToken, User  # noqa: E402
from app.models.user import ROLE_ADMIN, ROLE_INVENTARIO, ROLE_VENDEDOR  # noqa: E402
from app.services import settings_service  # noqa: E402

FAILURES = 0
CHECKS_RUN = 0
ADMIN_PASSWORD = "SecretCorePass999"
TABS = ("general", "notifications", "users", "security", "backup", "predictive", "integrations")
FIXTURE_MARKERS = (b"isabel.vivas@ferreteriapro.com", b"190.12.45.67", b"/backups/ferreteria_pro",
                   b"notificaciones@ferreteriapro.com", b"Ferreter\xc3\xada Pro C.A.")


@compiles(BigInteger, "sqlite")
def _compile_big_integer_for_sqlite(_type, _compiler, **_kwargs):
    return "INTEGER"


@listens_for(Engine, "connect")
def _configure_isolated_sqlite(dbapi_connection, _connection_record) -> None:
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")


def check(name: str, condition: bool, detail: str = "") -> None:
    global FAILURES, CHECKS_RUN
    CHECKS_RUN += 1
    if not condition:
        FAILURES += 1
    print(f"[{'OK' if condition else 'FALLO'}] {name}{f' — {detail}' if detail else ''}")


class IsolatedConfig:
    SECRET_KEY = secrets.token_urlsafe(32)
    APP_ENV = "testing"
    TESTING = True
    DEBUG = False
    PROPAGATE_EXCEPTIONS = False
    WTF_CSRF_ENABLED = False
    MAIL_ENABLED = False
    GOOGLE_CLIENT_ID = None
    GOOGLE_CLIENT_SECRET = None
    GOOGLE_REDIRECT_URI = None
    MYSQLDUMP_BIN = ""
    MYSQL_BIN = ""
    NOTIFICATIONS_DRY_RUN = True  # jamás correo/WhatsApp/SMS reales desde pruebas
    GOOGLE_DRIVE_CLIENT_ID = None
    GOOGLE_DRIVE_CLIENT_SECRET = None
    GOOGLE_DRIVE_REDIRECT_URI = None
    APP_VERSION = "0.1.0"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"check_same_thread": False, "timeout": 30}}


def login(client, username: str, password: str):
    response = client.post("/api/auth/login", json={"identifier": username, "password": password})
    if response.status_code != 200:
        raise RuntimeError(f"login {username} -> {response.status_code} {response.get_data(as_text=True)}")
    # Settings mutations use the same Flask-WTF token exposed to the browser.
    page = client.get("/settings?tab=general")
    csrf = re.search(rb'data-csrf="([^"]+)"', page.data)
    if not csrf:  # roles sin Configuración: el mismo token de sesión se expone en /profile
        csrf = re.search(rb'data-csrf="([^"]+)"', client.get("/profile").data)
    if csrf:
        client.environ_base["HTTP_X_CSRFTOKEN"] = csrf[1].decode()
    return response


def setup_app(temp_root: Path):
    IsolatedConfig.SQLALCHEMY_DATABASE_URI = "sqlite:///" + (temp_root / "settings_core.sqlite").as_posix()
    app = create_app(IsolatedConfig)
    app.instance_path = str(temp_root / "instance")
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    with app.app_context():
        db.create_all()
        admin = User(name="Admin", email="admin@core.test", username="core_admin", role=ROLE_ADMIN)
        admin.set_password(ADMIN_PASSWORD)
        inventario = User(name="Inventario", email="inv@core.test", username="core_inv", role=ROLE_INVENTARIO)
        inventario.set_password("inv123")
        vendedor = User(name="Vendedor", email="ven@core.test", username="core_ven", role=ROLE_VENDEDOR)
        vendedor.set_password("ven123")
        db.session.add_all([admin, inventario, vendedor])
        db.session.commit()
        ids = {"admin": admin.id, "inventario": inventario.id, "vendedor": vendedor.id}
    return app, ids


def test_live_pages(app) -> None:
    client = app.test_client()
    login(client, "core_admin", ADMIN_PASSWORD)
    for tab in TABS:
        r = client.get(f"/settings?tab={tab}")
        body = r.data
        check(f"live {tab} renderiza 200", r.status_code == 200, str(r.status_code))
        check(f"live {tab} usa shell Pro y sin shell legado",
              b"ix-search-input" in body and b"ix-bell" in body and "El Conejo".encode() in body
              and b"Sucursal" not in body and b'<span class="ix-brand-name">El Conejo C.A.</span>' not in body)
        check(f"live {tab} sin datos de maqueta", not any(m in body for m in FIXTURE_MARKERS))
        check(f"live {tab} carga ix_settings_live.js", b"ix_settings_live.js" in body and b"ix_settings.js" not in body)
    general = client.get("/settings?tab=general").data
    check("general muestra nombre real de la empresa", "Ferretería y Construcciones El Conejo C.A.".encode() in general)
    check("general: zona horaria y valores no disponibles", b"No disponible" in general and b"America/Caracas" in general)
    check("general: versión y motor de BD reales", b"0.1.0" in general and b"sqlite" in general)
    check("live ?ref=1 sin debug no expone fixture", not any(m in client.get("/settings?tab=users&ref=1").data for m in FIXTURE_MARKERS))
    check("vendedor no accede a /settings", client.get("/settings").status_code in (302, 403) if login(client, "core_ven", "ven123") else False)
    check("GET /settings rechaza POST", client.post("/settings").status_code == 405)


def test_settings_api(app, ids) -> None:
    client = app.test_client()
    for username, password in (("core_ven", "ven123"), ("core_inv", "inv123")):
        login(client, username, password)
        check(f"{username} GET /api/settings -> 403", client.get("/api/settings").status_code == 403)
        check(f"{username} PUT /api/settings -> 403",
              client.put("/api/settings", json={settings_service.COMPANY_NAME: "x"}).status_code == 403)
        check(f"{username} POST /api/settings/backups -> 403", client.post("/api/settings/backups").status_code == 403)
        client.post("/api/auth/logout")
    login(client, "core_admin", ADMIN_PASSWORD)
    r = client.get("/api/settings")
    check("admin GET /api/settings devuelve defaults", r.status_code == 200 and r.get_json()["settings"][settings_service.LOW_STOCK_ALERTS] is True
          and r.get_json()["available"] is True)
    r = client.put("/api/settings", json={"evil.key": 1})
    check("clave no soportada -> 400", r.status_code == 400, r.get_data(as_text=True))
    r = client.put("/api/settings", json={settings_service.COMPANY_NAME: "   "})
    check("nombre vacío -> 400", r.status_code == 400)
    r = client.put("/api/settings", json={settings_service.COMPANY_NAME: "x" * 121})
    check("nombre > 120 -> 400", r.status_code == 400)
    r = client.put("/api/settings", json={settings_service.LOW_STOCK_ALERTS: "sí"})
    check("toggle no booleano -> 400", r.status_code == 400)
    r = client.put("/api/settings", json={settings_service.PASSWORD_MIN_LENGTH: 3})
    check("longitud mínima < 6 -> 400", r.status_code == 400)
    r = client.put("/api/settings", json={settings_service.COMPANY_NAME: "Ferretería Prueba S.A.", settings_service.LOW_STOCK_ALERTS: False})
    check("PUT válido persiste", r.status_code == 200 and r.get_json()["settings"][settings_service.COMPANY_NAME] == "Ferretería Prueba S.A.")
    check("GET refleja el cambio", client.get("/api/settings").get_json()["settings"][settings_service.LOW_STOCK_ALERTS] is False)
    with app.app_context():
        from app.models import AppSetting
        row = db.session.get(AppSetting, settings_service.COMPANY_NAME)
        check("updated_by_user_id registrado", row is not None and row.updated_by_user_id == ids["admin"])
    check("página general muestra el nuevo nombre", "Ferretería Prueba S.A.".encode() in client.get("/settings?tab=general").data)
    alerts = client.get("/api/shell/alerts").get_json()
    check("campana desactivada cuando alerts.low_stock_enabled=false", alerts["count"] == 0 and alerts.get("disabled") is True)
    client.put("/api/settings", json={settings_service.LOW_STOCK_ALERTS: True})
    check("campana vuelve a evaluar stock", client.get("/api/shell/alerts").get_json().get("disabled") is not True)


def test_users_and_auth(app, ids) -> None:
    client = app.test_client()
    # A) registro público no permite elegir admin/inventario
    for role in (ROLE_ADMIN, ROLE_INVENTARIO):
        r = client.post("/api/auth/register", json={"name": "Intruso", "email": f"{role}@evil.test", "username": f"evil_{role}",
                                                    "password": "Clave12345", "role": role})
        check(f"registro público con rol {role} -> 403", r.status_code == 403, str(r.status_code))
    r = client.post("/api/auth/register", json={"name": "Nuevo", "email": "nuevo@core.test", "username": "nuevo_ven",
                                                "password": "Clave12345", "role": ROLE_VENDEDOR})
    check("registro público vendedor sigue funcionando", r.status_code == 201, r.get_data(as_text=True))
    with app.app_context():
        check("registro público jamás crea admin", User.query.filter_by(role=ROLE_ADMIN).count() == 1)
    client.post("/api/auth/logout")
    # admin crea/edita usuarios con la API existente
    login(client, "core_admin", ADMIN_PASSWORD)
    r = client.post("/api/users", json={"name": "Inv 2", "email": "inv2@core.test", "username": "inv2", "password": "Clave12345", "role": ROLE_INVENTARIO})
    check("admin crea usuario inventario", r.status_code == 201 and r.get_json()["user"]["role"] == ROLE_INVENTARIO)
    payload = r.get_json()["user"]
    check("payload de usuario no expone secretos", not any(k in payload for k in ("password", "password_hash", "token", "reset_token")))
    r = client.post("/api/users", json={"name": "Corto", "email": "corto@core.test", "username": "corto", "password": "abc", "role": ROLE_VENDEDOR})
    check("alta rechaza contraseña < mínimo", r.status_code == 400)
    # último admin: por HTTP el propio admin es rechazado (400, regla existente); el guard del servicio
    # cubre cualquier otra vía y devuelve 409 si no queda otro administrador activo.
    r = client.put(f"/api/users/{ids['admin']}", json={"role": ROLE_VENDEDOR})
    check("degradar al único admin (él mismo) rechazado", r.status_code == 400, str(r.status_code))
    r = client.put(f"/api/users/{ids['admin']}", json={"is_active": False})
    check("desactivar al único admin (él mismo) rechazado", r.status_code == 400, str(r.status_code))
    with app.app_context():
        from app.services.exceptions import ConflictError
        from app.services.rbac_service import guard_last_admin
        try:
            guard_last_admin(db.session.get(User, ids["admin"]))
            check("guard último admin -> 409", False, "no lanzó")
        except ConflictError as exc:
            check("guard último admin -> 409", exc.status_code == 409)
    with app.app_context():
        check("siempre queda un admin activo", User.query.filter_by(role=ROLE_ADMIN, is_active=True).count() >= 1)
    r = client.post("/api/users", json={"name": "Admin 2", "email": "admin2@core.test", "username": "admin2", "password": "Clave12345", "role": ROLE_ADMIN})
    admin2 = r.get_json()["user"]["id"]
    r = client.put(f"/api/users/{admin2}", json={"is_active": False})
    check("desactivar un admin cuando hay otro activo -> 200", r.status_code == 200, str(r.status_code))
    check("reactivar admin -> 200", client.put(f"/api/users/{admin2}", json={"is_active": True}).status_code == 200)
    check("no existe DELETE /api/users/<id>", client.delete(f"/api/users/{admin2}").status_code == 405)
    # B) sesión de usuario inactivo rechazada
    r = client.put(f"/api/users/{ids['vendedor']}", json={"is_active": False})
    check("admin desactiva vendedor", r.status_code == 200)
    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"identifier": "core_ven", "password": "ven123"})
    check("login de inactivo rechazado", r.status_code in (401, 403), str(r.status_code))
    ven = app.test_client()
    login(ven, "core_inv", "inv123")
    check("sesión activa funciona", ven.get("/api/auth/me").status_code == 200)
    admin_client = app.test_client()
    login(admin_client, "core_admin", ADMIN_PASSWORD)
    admin_client.put(f"/api/users/{ids['inventario']}", json={"is_active": False})
    check("sesión existente de usuario desactivado queda rechazada (load_user)", ven.get("/api/auth/me").status_code == 401)
    admin_client.put(f"/api/users/{ids['inventario']}", json={"is_active": True})
    admin_client.put(f"/api/users/{ids['vendedor']}", json={"is_active": True})


def test_change_password_and_policy(app) -> None:
    client = app.test_client()
    check("change-password anónimo -> 401", client.post("/api/auth/change-password", json={}).status_code == 401)
    login(client, "core_ven", "ven123")
    r = client.post("/api/auth/change-password", json={"current_password": "mala", "new_password": "NuevaClave1", "confirm_password": "NuevaClave1"})
    check("contraseña actual incorrecta -> 400", r.status_code == 400 and "ven123" not in r.get_data(as_text=True))
    r = client.post("/api/auth/change-password", json={"current_password": "ven123", "new_password": "NuevaClave1", "confirm_password": "Otra"})
    check("confirmación distinta -> 400", r.status_code == 400)
    r = client.post("/api/auth/change-password", json={"current_password": "ven123", "new_password": "abc", "confirm_password": "abc"})
    check("nueva < mínimo -> 400", r.status_code == 400)
    r = client.post("/api/auth/change-password", json={"current_password": "ven123", "new_password": "ven123", "confirm_password": "ven123"})
    check("nueva igual a la actual -> 400", r.status_code == 400)
    r = client.post("/api/auth/change-password", json={"current_password": "ven123", "new_password": "NuevaClave1", "confirm_password": "NuevaClave1"})
    check("cambio válido -> 200 sin filtrar hash", r.status_code == 200 and "hash" not in r.get_data(as_text=True).lower())
    client.post("/api/auth/logout")
    check("login con la nueva contraseña", client.post("/api/auth/login", json={"identifier": "core_ven", "password": "NuevaClave1"}).status_code == 200)
    check("login con la vieja falla", client.post("/api/auth/login", json={"identifier": "core_ven", "password": "ven123"}).status_code == 401)
    # política configurable aplicada en alta, cambio y recuperación
    admin = app.test_client()
    login(admin, "core_admin", ADMIN_PASSWORD)
    admin.put("/api/settings", json={settings_service.PASSWORD_MIN_LENGTH: 12})
    r = admin.post("/api/users", json={"name": "P", "email": "p@core.test", "username": "pol", "password": "Solo11chars", "role": ROLE_VENDEDOR})
    check("alta respeta mínimo 12", r.status_code == 400, str(r.status_code))
    r = client.post("/api/auth/change-password", json={"current_password": "NuevaClave1", "new_password": "Solo11chars", "confirm_password": "Solo11chars"})
    check("cambio respeta mínimo 12", r.status_code == 400, str(r.status_code))
    with app.app_context():
        from datetime import datetime, timedelta
        from app.services.password_reset_service import _hash_token
        user = User.query.filter_by(username="core_ven").first()
        raw = secrets.token_urlsafe(32)
        db.session.add(PasswordResetToken(user_id=user.id, email=user.email, token_hash=_hash_token(raw),
                                          expires_at=datetime.utcnow() + timedelta(minutes=30)))
        db.session.commit()
    r = client.post("/api/auth/password-reset/confirm", json={"token": raw, "new_password": "Solo11chars"})
    check("recuperación respeta mínimo 12", r.status_code == 400, str(r.status_code))
    r = client.post("/api/auth/password-reset/confirm", json={"token": raw, "new_password": "ClaveLarga123456", "confirm_password": "ClaveLarga123456"})
    check("recuperación acepta contraseña válida", r.status_code == 200, r.get_data(as_text=True))
    admin.put("/api/settings", json={settings_service.PASSWORD_MIN_LENGTH: 6})


def test_backups(app) -> None:
    client = app.test_client()
    login(client, "core_admin", ADMIN_PASSWORD)
    r = client.get("/api/settings/backups")
    data = r.get_json()
    check("GET backups: estado honesto sin mysqldump", r.status_code == 200 and data["available"] is False and data["history"] == []
          and data["mysqldump_configured"] is False)
    r = client.post("/api/settings/backups")
    check("POST backups sin MySQL/mysqldump -> 503 sin crear filas", r.status_code == 503 and client.get("/api/settings/backups").get_json()["count"] == 0)
    check("restore no disponible sin cliente mysql (estado honesto)", data["restore_available"] is False)
    check("restore-preview de respaldo inexistente -> 404", client.get("/api/settings/backups/999/restore-preview").status_code == 404)
    check("backups es solo lectura salvo POST", client.delete("/api/settings/backups").status_code == 405)
    page = client.get("/settings?tab=backup").data
    check("pestaña backup honesta sin historial", b"st-backup-create" in page and b"st-restore-button" in page and b"/backups/ferreteria_pro" not in page)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="settings_core_") as tmp:
        app, ids = setup_app(Path(tmp))
        test_live_pages(app)
        test_settings_api(app, ids)
        test_users_and_auth(app, ids)
        test_change_password_and_policy(app)
        test_backups(app)
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
    print(f"\n{CHECKS_RUN - FAILURES}/{CHECKS_RUN} comprobaciones OK")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
