#!/usr/bin/env python3
"""Rol soporte_ti. SQLite temporal; no toca ferreteria_conejo_tesis.

Uso: python scripts/test_soporte_ti.py
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from test_products_core import check, login, setup_app  # noqa: E402

from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402
from app.models.backup import BACKUP_KIND_DATABASE, BACKUP_STATUS_COMPLETED, Backup  # noqa: E402
from app.models.user import ROLE_SOPORTE_TI  # noqa: E402
from app.services import backup_service, rbac_service  # noqa: E402
from app.utils.navigation import home_path, settings_tab_allowed  # noqa: E402
from app.utils.permissions import ROLE_PERMISSIONS  # noqa: E402

EXPECTED = frozenset({"settings:read", "security:manage", "backups:create", "backups:manage"})
PASSWORD = "Soporte123"


def _denied(response) -> bool:
    text = response.get_data(as_text=True)
    return response.status_code == 403 and (
        "No tiene permisos para acceder a esta sección." in text or "Acceso denegado" in text
    )


def _sidebar(html: str) -> str:
    at = html.find('id="ix-sidebar"')
    if at < 0:
        return ""
    start = html.rfind("<aside", 0, at + 1)
    end = html.find("</aside>", at)
    return html[start:end] if start >= 0 and end > start else ""


def main() -> int:
    check("mapa estático exacto", ROLE_PERMISSIONS[ROLE_SOPORTE_TI] == EXPECTED)
    check("aterrizaje explícito de admin, inventario y soporte",
          home_path("soporte_ti") == "/settings" and home_path("admin") == "/dashboard"
          and home_path("inventario") == "/dashboard")

    root = Path(tempfile.mkdtemp(prefix="soporte-ti-"))
    app, admin, inventario, vendedor, ids = setup_app(root)
    with app.app_context():
        rbac_service.ensure_seeded()
        stored = {role.slug: set(role.permission_codes) for role in db.session.query(rbac_service.Role).all()}
        check("semilla: soporte_ti tiene exactamente cuatro permisos", stored.get("soporte_ti") == EXPECTED)
        check("semilla: admin, inventario y vendedor no cambian",
              stored["admin"] == ROLE_PERMISSIONS["admin"] and stored["inventario"] == ROLE_PERMISSIONS["inventario"]
              and stored["vendedor"] == ROLE_PERMISSIONS["vendedor"])
        check("pestañas por permiso, sin placeholders",
              settings_tab_allowed("soporte_ti", "general") and settings_tab_allowed("soporte_ti", "security")
              and settings_tab_allowed("soporte_ti", "backup") and settings_tab_allowed("soporte_ti", "notifications")
              and not settings_tab_allowed("soporte_ti", "users")
              and not settings_tab_allowed("soporte_ti", "integrations")
              and not settings_tab_allowed("soporte_ti", "predictive")
              and settings_tab_allowed("admin", "users") and settings_tab_allowed("admin", "integrations")
              and settings_tab_allowed("admin", "predictive")
              and home_path("vendedor") == "/sales")
        user = User(name="Soporte", email="soporte@core.test", username="core_soporte", role=ROLE_SOPORTE_TI)
        user.set_password(PASSWORD)
        db.session.add(user)
        db.session.commit()
        soporte_id = user.id
        admin_id = ids["admin_id"]

    soporte = app.test_client()
    body = login(soporte, "core_soporte", PASSWORD).get_json()
    check("login soporte_ti -> /settings", body.get("home") == "/settings" and "/settings" in soporte.get("/login").headers.get("Location", ""))

    page = soporte.get("/settings")
    html = page.get_data(as_text=True)
    nav = _sidebar(html)
    check("abre General con estado de base y disco", page.status_code == 200 and "Motor de base de datos" in html and "Disco del servidor" in html)
    absent = {
        'href="/dashboard"': nav, 'href="/products"': nav, 'href="/inventory"': nav, 'href="/sales"': nav,
        "Órdenes de compra": nav, "Conciliación": nav, "Pronóstico de demanda": nav,
        "Usuarios y roles": html, "Integraciones": html, "tab=predictive": html,
    }
    present = {"Seguridad": html, "Respaldo": html, "Notificaciones": html}
    missing = [label for label, source in absent.items() if label in source]
    missing += [label for label, source in present.items() if label not in source]
    check("sidebar sin módulos operativos", not missing, ", ".join(missing))
    check("admin conserva Usuarios, Integraciones y Predictivo",
          "Usuarios y roles" in admin.get("/settings", query_string={"tab": "users"}).get_data(as_text=True)
          and "Integraciones" in admin.get("/settings", query_string={"tab": "integrations"}).get_data(as_text=True))

    check("PUT settings -> 403", soporte.put("/api/settings", json={"company.name": "No"}).status_code == 403)
    security = soporte.get("/settings", query_string={"tab": "security"})
    import re
    page_token = re.search(r'data-csrf="([^"]+)"', security.get_data(as_text=True))
    if page_token:  # como el navegador: toda mutación de Configuración envía el token de la página (CSRF obligatorio)
        soporte.environ_base["HTTP_X_CSRFTOKEN"] = page_token.group(1)
    sessions = soporte.get("/api/settings/security/sessions")
    history = soporte.get("/api/settings/security/login-history")
    check("Seguridad y datos propios", security.status_code == 200 and sessions.status_code == 200 and history.status_code == 200)
    with app.app_context():
        from app.models.security import UserSession
        foreign = UserSession.query.filter(UserSession.user_id == admin_id).first()
        foreign_id = foreign.id if foreign is not None else None
    if foreign_id is not None:
        import re
        token = re.search(r'data-csrf="([^"]+)"', security.get_data(as_text=True))
        headers = {"X-CSRFToken": token.group(1)} if token else {}
        stolen = soporte.delete(f"/api/settings/security/sessions/{foreign_id}", headers=headers)
        check("no cierra sesiones ajenas", stolen.status_code == 404, stolen.get_data(as_text=True)[:180])
    else:
        check("no hay sesión ajena que cerrar en esta suite", True)

    listed = soporte.get("/api/settings/backups")
    created = soporte.post("/api/settings/backups", json={"kind": "database"})
    check("listar respaldos y pasar el permiso de creación", listed.status_code == 200 and created.status_code == 503,
          f"lista={listed.status_code} alta={created.status_code}")
    check("vendedor no crea respaldos", vendedor.post("/api/settings/backups", json={"kind": "database"}).status_code == 403)

    payload = b"respaldo-sqlite-de-prueba"
    digest = hashlib.sha256(payload).hexdigest()
    with app.app_context():
        folder = backup_service.backup_dir()
        folder.mkdir(parents=True, exist_ok=True)
        filename = "sqlite_prueba_database.sql"
        (folder / filename).write_bytes(payload)
        row = Backup(filename=filename, sha256=digest, byte_size=len(payload), database_name="sqlite",
                     kind=BACKUP_KIND_DATABASE, status=BACKUP_STATUS_COMPLETED, created_by_user_id=soporte_id)
        db.session.add(row)
        db.session.commit()
        backup_id = row.id
    downloaded = soporte.get(f"/api/settings/backups/{backup_id}/download")
    downloaded_body = downloaded.data
    downloaded.close()
    verified = soporte.post(f"/api/settings/backups/{backup_id}/verify")
    check("descarga y verifica con backups:manage",
          downloaded.status_code == 200 and downloaded_body == payload and verified.status_code == 200 and verified.json["ok"] is True)
    check("restaurar -> 403", soporte.post(f"/api/settings/backups/{backup_id}/restore", json={"confirmation": "RESTAURAR"}).status_code == 403)
    check("backups:manage también elimina el archivo de respaldo",
          soporte.delete(f"/api/settings/backups/{backup_id}").status_code == 200
          and vendedor.delete(f"/api/settings/backups/{backup_id}").status_code == 403)
    check("Drive exige backups:manage",
          vendedor.get("/api/settings/backups/drive/connect").status_code == 403
          and soporte.get("/api/settings/backups/drive/connect").status_code != 403)

    check("usuarios y cambios de rol -> 403",
          soporte.get("/api/users").status_code == 403
          and soporte.post("/api/settings/roles", json={"name": "Extra", "permissions": []}).status_code == 403)
    for path in ("/dashboard", "/sales", "/inventory", "/purchases", "/reconciliation", "/predictive/demand"):
        check(f"URL {path} -> 403", _denied(soporte.get(path)))
    for path in ("/api/sales", "/api/inventory/movements", "/api/purchases", "/api/reconciliation", "/api/predictions/readiness"):
        check(f"API {path} -> 403", soporte.get(path).status_code == 403)

    check("los otros tres roles conservan su aterrizaje",
          login(admin, "core_admin", "SecretCorePass999").get_json()["home"] == "/dashboard"
          and login(inventario, "core_inv", "inv123").get_json()["home"] == "/dashboard"
          and login(vendedor, "core_ven", "ven123").get_json()["home"] == "/sales")

    from test_products_core import CHECKS_RUN, FAILURES
    print(f"\n{CHECKS_RUN} comprobaciones, {FAILURES} fallos")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
