#!/usr/bin/env python3
"""Corrección de Configuración (18 oct 2026): CSRF, reemplazo 2FA, acciones por permiso y recuperación.

SQLite temporal y archivos sintéticos bajo una carpeta temporal; sin MySQL, red ni datos reales.
Los respaldos de base de datos y completos (mysqldump) se prueban en MariaDB desechable aparte.

Uso: python scripts/test_settings_config_fix_20261018.py
"""
from __future__ import annotations

import io
import json
import re
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_settings_core as core  # noqa: E402

import pyotp  # noqa: E402
from PIL import Image  # noqa: E402

from app.extensions import db  # noqa: E402
from app.models import Backup, Notification, Role, User, UserMfa  # noqa: E402
from app.services import backup_service, mfa_service, rbac_service, settings_service  # noqa: E402
from app.services.exceptions import ValidationError  # noqa: E402

check = core.check
PASSWORD = "SyntheticPass123!"
ROLES = ("admin", "soporte_ti", "inventario", "asistente", "vendedor")
EXTRA = {"vendedor_clave": "vendedor", "admin_mfa": "admin"}  # cuentas dedicadas: cambio de clave y 2FA


def at(moment: float):
    """Fija el reloj solo dentro de mfa_service (no el módulo time global, que usan sesión y cookies)."""
    return patch.object(mfa_service, "time", SimpleNamespace(time=lambda: moment))


def setup(folder: Path):
    app, ids = core.setup_app(folder)
    app.config.update(DEMO_WITHDRAW_CUSTOMER_SALES=True, BACKUP_SCHEDULER="unsupported",
                      COMPANY_LOGO_DIR=str(folder / "instance" / "uploads" / "company"))
    backup_service.project_root = lambda: folder  # raíz sintética: uploads/ e instance/ viven en la carpeta temporal
    with app.app_context():
        rbac_service.ensure_seeded()
        for slug in ROLES + tuple(EXTRA):
            user = User(name=f"Sintético {slug}", username=f"syn_{slug}", email=f"{slug}@example.invalid", role=EXTRA.get(slug, slug))
            user.set_password(PASSWORD)
            db.session.add(user)
        db.session.commit()
    return app, ids


def client_for(app, username, password=PASSWORD, token=True):
    client = app.test_client()
    core.login(client, username, password)
    if not token:
        client.environ_base.pop("HTTP_X_CSRFTOKEN", None)
    return client


def png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 40), "#1268e8").save(output, "PNG")
    return output.getvalue()


def test_csrf(app) -> None:
    admin = client_for(app, "core_admin", core.ADMIN_PASSWORD)
    bare = client_for(app, "core_admin", core.ADMIN_PASSWORD, token=False)
    with app.app_context():
        roles_before = Role.query.count()
        users_before = User.query.count()
        target = User.query.filter_by(username="syn_vendedor").first().id
        note = Notification(user_id=User.query.filter_by(username="core_admin").first().id, event="security",
                            title="Aviso sintético", message="Sin leer")
        db.session.add(note)
        db.session.commit()
        note_id = note.id
    cases = [
        ("PUT ajustes", "put", "/api/settings", {"json": {"company.name": "Atacante"}}),
        ("POST rol", "post", "/api/settings/roles", {"json": {"name": "Rol CSRF", "permissions": ["products:read"]}}),
        ("POST prueba de avisos", "post", "/api/settings/notifications/test", {"json": {"channels": ["app"]}}),
        ("POST marcar todas", "post", "/api/settings/notifications/read-all", {}),
        ("POST marcar todas (formulario)", "post", "/api/settings/notifications/read-all", {"data": {}}),
        ("POST marcar una", "post", f"/api/settings/notifications/{note_id}/read", {}),
        ("POST enrolar 2FA", "post", "/api/auth/mfa/enroll", {}),
        ("POST alta de usuario", "post", "/api/users", {"json": {"name": "X", "email": "x@example.invalid", "username": "csrf_x",
                                                              "password": "Synthetic123!", "role": "vendedor"}}),
        ("PUT usuario", "put", f"/api/users/{target}", {"json": {"name": "Cambiado por CSRF"}}),
        ("POST respaldo", "post", "/api/settings/backups", {"json": {"kind": "files"}}),
        ("POST cambio de contraseña", "post", "/api/auth/change-password",
         {"json": {"current_password": core.ADMIN_PASSWORD, "new_password": "Otra12345!", "confirm_password": "Otra12345!"}}),
    ]
    for label, method, url, kwargs in cases:
        check(f"CSRF ausente rechaza {label}", getattr(bare, method)(url, **kwargs).status_code == 400)
        check(f"CSRF inválido rechaza {label}",
              getattr(admin, method)(url, headers={"X-CSRFToken": "invalido"}, **kwargs).status_code == 400)
    with app.app_context():
        check("sin token no cambió datos",
              settings_service.get(settings_service.COMPANY_NAME) != "Atacante" and Role.query.count() == roles_before
              and User.query.count() == users_before and db.session.get(Notification, note_id).read_at is None
              and db.session.get(User, target).name != "Cambiado por CSRF"
              and UserMfa.query.filter(UserMfa.pending_secret_encrypted.isnot(None)).count() == 0
              and Backup.query.count() == 0)
    check("con token: PUT ajustes 200", admin.put("/api/settings", json={"company.name": "Empresa sintética"}).status_code == 200)
    check("con token: rol 201", admin.post("/api/settings/roles", json={"name": "Rol válido", "permissions": ["products:read"]}).status_code == 201)
    check("con token: prueba app 200", admin.post("/api/settings/notifications/test", json={"channels": ["app"]}).status_code == 200)
    check("con token: marcar una 200", admin.post(f"/api/settings/notifications/{note_id}/read").status_code == 200)
    check("con token: marcar todas 200", admin.post("/api/settings/notifications/read-all").status_code == 200)
    check("con token: PUT usuario 200", admin.put(f"/api/users/{target}", json={"name": "Vendedor sintético"}).status_code == 200)
    anonymous = app.test_client()
    check("anónimo sigue recibiendo 401 antes que CSRF",
          anonymous.post("/api/auth/mfa/enroll").status_code == 401
          and anonymous.post("/api/auth/change-password", json={}).status_code == 401)
    seller = client_for(app, "syn_vendedor", token=False)
    check("sin permiso sigue recibiendo 403 antes que CSRF", seller.post("/api/settings/roles", json={}).status_code == 403
          and seller.post("/api/users", json={}).status_code == 403)
    check("login sin token sigue funcionando", app.test_client().post(
        "/api/auth/login", json={"identifier": "syn_inventario", "password": PASSWORD}).status_code == 200)
    seller = client_for(app, "syn_vendedor_clave")
    changed = seller.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "Vendedor12345!",
                                                             "confirm_password": "Vendedor12345!"})
    check("rol sin Configuración cambia su contraseña con el token de /profile", changed.status_code == 200)
    check("contraseña cambiada sirve para entrar", app.test_client().post(
        "/api/auth/login", json={"identifier": "syn_vendedor_clave", "password": "Vendedor12345!"}).status_code == 200)


def test_mfa_replacement(app) -> None:
    client = client_for(app, "syn_admin_mfa")
    other = client_for(app, "syn_admin_mfa")  # segunda sesión de la misma cuenta, abierta antes de activar 2FA
    now = time.time()
    with at(now):
        first = client.post("/api/auth/mfa/enroll").get_json()
        check("enrolamiento inicial sin cuerpo", "setup_key" in first)
        check("confirmación inicial", client.post("/api/auth/mfa/confirm", json={"code": pyotp.TOTP(first["setup_key"]).at(now)}).status_code == 200)
    old = pyotp.TOTP(first["setup_key"])
    with app.app_context():
        uid = User.query.filter_by(username="syn_admin_mfa").first().id
        secret_before = db.session.get(UserMfa, uid).secret_encrypted
    later = now + 60
    with at(later):
        check("reemplazo sin reautenticar rechazado", client.post("/api/auth/mfa/enroll").status_code == 400)
        check("reemplazo con código incorrecto rechazado",
              client.post("/api/auth/mfa/enroll", json={"code": "000000", "current_password": PASSWORD}).status_code == 400)
        check("reemplazo sin contraseña rechazado",
              client.post("/api/auth/mfa/enroll", json={"code": old.at(later)}).status_code == 400)
    with app.app_context():
        row = db.session.get(UserMfa, uid)
        check("rechazos no crearon secreto pendiente ni tocaron el factor",
              row.pending_secret_encrypted is None and row.secret_encrypted == secret_before)
    with at(later + 30):
        replacement = client.post("/api/auth/mfa/enroll", json={"code": old.at(later + 30), "current_password": PASSWORD})
        check("reemplazo con código actual y contraseña", replacement.status_code == 200)
        new = pyotp.TOTP(replacement.get_json()["setup_key"])
        with app.app_context():
            check("el factor anterior sigue vigente hasta confirmar", db.session.get(UserMfa, uid).secret_encrypted == secret_before)
        check("otra sesión no puede confirmar el reemplazo",
              other.post("/api/auth/mfa/confirm", json={"code": new.at(later + 30)}).status_code == 400)
        confirmed = client.post("/api/auth/mfa/confirm", json={"code": new.at(later + 30)})
        body = confirmed.get_json() or {}
        check("confirmación en el mismo paso TOTP de la reautenticación",
              confirmed.status_code == 200 and body.get("replaced") is True and len(body.get("recovery_codes", [])) == 10)
    with app.app_context(), at(later + 120):
        user = db.session.get(User, uid)
        try:
            mfa_service.verify_code(user, old.at(later + 120))
            check("el código del factor anterior deja de servir", False)
        except ValidationError:
            check("el código del factor anterior deja de servir", True)
        check("el nuevo factor verifica", mfa_service.verify_code(user, new.at(later + 120)) == "totp")
        db.session.commit()
    with at(later + 180):
        regenerated = client.post("/api/auth/mfa/recovery-codes", json={"code": new.at(later + 180), "current_password": PASSWORD})
        check("regenerar códigos sigue exigiendo y aceptando reautenticación", regenerated.status_code == 200
              and len(regenerated.get_json()["recovery_codes"]) == 10)
    with at(later + 240):
        trusted = client.post("/api/auth/mfa/devices", json={"code": new.at(later + 240), "label": "Equipo sintético"})
        check("dispositivo confiable sigue funcionando", trusted.status_code == 200 and len(trusted.get_json()["items"]) == 1)
    sessions = client.get("/api/settings/security/sessions")
    check("sesiones propias siguen listándose", sessions.status_code == 200, f"{sessions.status_code} {sessions.get_data(as_text=True)[:200]}")


def _buttons(html: str) -> set[str]:
    return set(re.findall(r'id="(st-save|st-reset|st-test-notifications|st-backup-create|st-schedule-apply|st-role-new|st-user-new)"', html))


def test_roles_and_actions(app) -> None:
    clients = {slug: client_for(app, f"syn_{slug}") for slug in ROLES}
    allowed = {"admin": set(core.TABS), "soporte_ti": {"general", "notifications", "security", "backup"}}
    for slug, client in clients.items():
        for tab in core.TABS:
            status = client.get(f"/settings?tab={tab}").status_code
            check(f"{slug} pestaña {tab}", status == 200 if tab in allowed.get(slug, set()) else status in (302, 403), str(status))
    admin, support = clients["admin"], clients["soporte_ti"]
    page = {tab: admin.get(f"/settings?tab={tab}").get_data(as_text=True) for tab in core.TABS}
    check("admin: Guardar y Restablecer en General", {"st-save", "st-reset"} <= _buttons(page["general"]))
    check("admin: Probar notificaciones visible", "st-test-notifications" in _buttons(page["notifications"]))
    check("predictivo e integraciones sin botones inertes",
          not ({"st-save", "st-reset"} & (_buttons(page["predictive"]) | _buttons(page["integrations"]))))
    check("predictivo muestra parámetros reales y navegación al módulo",
          "7, 30, 90 días" in page["predictive"] and "/predictive/demand" in page["predictive"] and "Ir a mi perfil" not in page["predictive"])
    check("integraciones muestran proveedores sin configurar",
          page["integrations"].count("Sin configurar") >= 5 and "Ir a mi perfil" not in page["integrations"])
    check("usuarios: guardar permisos del rol", "Guardar permisos del rol" in page["users"])
    check("usuarios: sin permisos comerciales retirados", 'data-permission="sales:' not in page["users"]
          and 'value="sales:' not in page["users"])
    check("notificaciones: sin evento de ventas retirado", "Nueva venta / pedido" not in page["notifications"])
    support_pages = {tab: support.get(f"/settings?tab={tab}").get_data(as_text=True) for tab in allowed["soporte_ti"]}
    for tab, html in support_pages.items():
        check(f"soporte: sin Guardar/Restablecer en {tab}", not ({"st-save", "st-reset"} & _buttons(html)))
    check("soporte: ajustes marcados de solo lectura donde existen",
          all("Ajustes de solo lectura" in support_pages[tab] for tab in ("general", "notifications", "backup")))
    check("soporte: sin Probar notificaciones", "st-test-notifications" not in _buttons(support_pages["notifications"]))
    check("soporte: crear y registrar tarea visibles", {"st-backup-create", "st-schedule-apply"} <= _buttons(support_pages["backup"]))
    check("soporte: restaurar deshabilitado y explicado",
          re.search(r'id="st-restore-button"[^>]*disabled', support_pages["backup"]) is not None
          and "Requiere el permiso «Restaurar respaldos»" in support_pages["backup"])
    check("soporte: formulario marcado como solo lectura", 'data-can-write="0"' in support_pages["general"])
    check("backend sigue negando a soporte", support.put("/api/settings", json={"company.name": "X"}).status_code == 403
          and support.post("/api/settings/notifications/test", json={"channels": ["app"]}).status_code == 403
          and support.post("/api/settings/backups/1/restore", json={"confirmation": "RESTAURAR"}).status_code == 403)
    catalog = admin.get("/api/settings/roles").get_json()
    codes = {p["code"] for group in catalog["permissions"] for p in group["permissions"]}
    check("catálogo API sin permisos retirados", not any(code.startswith("sales:") for code in codes) and "customers:read" in codes)
    custom = admin.post("/api/settings/roles", json={"name": "Rol todo", "permissions": ["products:read"]}).get_json()["role"]
    everything = admin.put(f"/api/settings/roles/{custom['id']}", json={"permissions": sorted(codes)})
    check("«Seleccionar todo» del catálogo visible se puede guardar", everything.status_code == 200)
    check("el backend sigue rechazando permisos retirados",
          admin.put(f"/api/settings/roles/{custom['id']}", json={"permissions": ["sales:read"]}).status_code == 403)
    with app.app_context():
        stored = {slug: sorted(rbac_service.permissions_for(slug)) for slug in ROLES}
    from app.utils.permissions import ROLE_PERMISSIONS, WITHDRAWN_CUSTOMER_SALES
    check("los cinco roles conservan sus permisos", all(
        set(stored[slug]) == set(ROLE_PERMISSIONS[slug]) - WITHDRAWN_CUSTOMER_SALES for slug in ROLES))
    overview = admin.get("/api/settings/notifications/overview").get_json()
    check("overview sin evento de ventas", "sales" not in overview["matrix"] and all(e["key"] != "sales" for e in overview["events"]))
    with app.app_context():
        uid = User.query.filter_by(username="syn_admin").first().id
        db.session.add(Notification(user_id=uid, event="sales", title="Venta histórica", message="Registro previo"))
        db.session.commit()
    inbox = admin.get("/api/settings/notifications?per_page=50").get_json()
    check("avisos históricos de ventas se conservan en la bandeja", any(item["event"] == "sales" for item in inbox["items"]))


def test_save_reset(app) -> None:
    admin = client_for(app, "core_admin", core.ADMIN_PASSWORD)
    check("guardar nombre", admin.put("/api/settings", json={"company.name": "Ferretería sintética"}).status_code == 200
          and admin.get("/api/settings").get_json()["settings"]["company.name"] == "Ferretería sintética")
    reset = admin.post("/api/settings/reset", json={"tab": "general"})
    check("restablecer General", reset.status_code == 200
          and reset.get_json()["settings"]["company.name"] == settings_service.SUPPORTED[settings_service.COMPANY_NAME][0])
    check("guardar y restablecer política", admin.put("/api/settings", json={"security.password_min_length": 8}).status_code == 200
          and admin.post("/api/settings/reset", json={"tab": "security"}).get_json()["settings"]["security.password_min_length"] == 6)
    check("restablecer notificaciones no borra la bandeja", admin.post("/api/settings/reset", json={"tab": "notifications"}).status_code == 200)


def test_backups(app, folder: Path) -> None:
    admin = client_for(app, "core_admin", core.ADMIN_PASSWORD)
    products = folder / "uploads" / "products"
    products.mkdir(parents=True, exist_ok=True)
    (products / "sintetico.txt").write_text("antes", encoding="utf-8")
    uploaded = admin.put("/api/settings", data={"settings": json.dumps({"company.name": "Con logo"}),
                                                "logo": (io.BytesIO(png()), "logo.png", "image/png")})
    check("logo sintético guardado", uploaded.status_code == 200)
    logo_name = uploaded.get_json()["settings"]["company.logo"]
    logo_path = folder / "instance" / "uploads" / "company" / logo_name
    original_logo = logo_path.read_bytes()
    created = admin.post("/api/settings/backups", json={"kind": "files"})
    check("respaldo de archivos 201", created.status_code == 201, str(created.status_code))
    backup = created.get_json()["backup"]
    with app.app_context():
        record = db.session.get(Backup, backup["id"])
        path = backup_service.backup_path(record)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
    check("el respaldo incluye el logo de la empresa", f"files/company_logo/{logo_name}" in names
          and "company_logo" in manifest["file_roots"])
    check("database y full siguen exigiendo mysqldump (503 en SQLite)",
          admin.post("/api/settings/backups", json={"kind": "database"}).status_code == 503
          and admin.post("/api/settings/backups", json={"kind": "full"}).status_code == 503)
    logo_path.unlink()
    shutil.rmtree(products)  # carpeta completa desaparecida
    restored = admin.post(f"/api/settings/backups/{backup['id']}/restore", json={"confirmation": "RESTAURAR"})
    check("restauración con carpeta desaparecida", restored.status_code == 200, restored.get_data(as_text=True)[:160])
    check("carpeta recreada y contenido recuperado", (products / "sintetico.txt").read_text(encoding="utf-8") == "antes")
    check("logo recuperado byte a byte", logo_path.is_file() and logo_path.read_bytes() == original_logo)
    check("logo servido tras restaurar", admin.get("/api/settings/company-logo").status_code == 200)
    check("se registró el respaldo de seguridad previo", any(item["source"] == "safety" for item in admin.get("/api/settings/backups").get_json()["history"]))
    (products / "sintetico.txt").write_text("despues", encoding="utf-8")
    original = path.read_bytes()
    path.write_bytes(original + b"alterado")
    verify = admin.post(f"/api/settings/backups/{backup['id']}/verify").get_json()
    check("alteración detectada por SHA-256", verify["ok"] is False and verify["status"] == "corrupt")
    refused = admin.post(f"/api/settings/backups/{backup['id']}/restore", json={"confirmation": "RESTAURAR"})
    check("respaldo corrupto no se restaura y no cambia datos", refused.status_code == 409
          and (products / "sintetico.txt").read_text(encoding="utf-8") == "despues")
    path.write_bytes(original)
    check("archivo original vuelve a verificar", admin.post(f"/api/settings/backups/{backup['id']}/verify").get_json()["ok"] is True)
    check("confirmación incorrecta rechazada", admin.post(f"/api/settings/backups/{backup['id']}/restore",
                                                          json={"confirmation": "restaurar"}).status_code == 400)
    unknown = path.with_name("desconocido.zip")
    with zipfile.ZipFile(unknown, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"kind": "files", "database_name": record.database_name,
                                                      "file_roots": ["otra_carpeta"]}))
        archive.writestr("files/otra_carpeta/x.txt", "x")
    with app.app_context():
        fake = Backup(filename=unknown.name, sha256=backup_service._sha256(unknown), byte_size=unknown.stat().st_size,
                      database_name=record.database_name, kind="files", status="completed",
                      created_by_user_id=User.query.filter_by(username="core_admin").first().id)
        db.session.add(fake)
        db.session.commit()
        fake_id, before = fake.id, Backup.query.count()
    blocked = admin.post(f"/api/settings/backups/{fake_id}/restore", json={"confirmation": "RESTAURAR"})
    with app.app_context():
        check("carpeta desconocida bloquea antes de crear la copia previa", blocked.status_code == 400
              and Backup.query.count() == before and (products / "sintetico.txt").read_text(encoding="utf-8") == "despues")
    # Cambio de carpeta global con respaldos registrados en la carpeta vigente.
    new_dir = folder / "instance" / "otra_carpeta_respaldos"
    current = admin.get("/api/settings/backups").get_json()["directory"]
    moved = admin.put("/api/settings", json={"backup.directory": str(new_dir)})
    check("cambiar carpeta con respaldos sin copiar se rechaza", moved.status_code == 400
          and admin.get("/api/settings").get_json()["settings"]["backup.directory"] == "")
    for item in Path(current).iterdir():
        if item.is_file():
            shutil.copy2(item, new_dir / item.name)
    check("tras copiar los archivos el cambio se acepta", admin.put("/api/settings", json={"backup.directory": str(new_dir)}).status_code == 200)
    check("los respaldos siguen verificando en la nueva carpeta",
          admin.post(f"/api/settings/backups/{backup['id']}/verify").get_json()["ok"] is True)
    for item in list(Path(current).iterdir()):
        if item.is_file():
            item.unlink()
    check("volver a la carpeta por defecto también se protege",
          admin.post("/api/settings/reset", json={"tab": "backup"}).status_code == 400)
    check("ningún respaldo se eliminó", all(item["status"] != "deleted" for item in admin.get("/api/settings/backups").get_json()["history"]))


def test_role_description_migration(app) -> None:
    import migrate_role_descriptions_20261018 as migration

    with app.app_context():
        engine = db.engine
        with engine.begin() as connection:
            connection.exec_driver_sql("UPDATE roles SET description='Ventas, clientes, cotizaciones, pedidos y notas de entrega.' WHERE slug='vendedor'")
            links_before = connection.exec_driver_sql("SELECT role_id, permission_id FROM role_permissions ORDER BY 1, 2").fetchall()
        with engine.begin() as connection:
            changes = migration.plan(connection, rbac_service.SYSTEM_ROLE_DESCRIPTIONS)
            check("plan detecta la descripción antigua del vendedor", [c["slug"] for c in changes] == ["vendedor"])
            check("aplicar actualiza solo esa descripción", migration.apply_changes(connection, changes) == 1)
        with engine.begin() as connection:
            row = connection.exec_driver_sql("SELECT description FROM roles WHERE slug='vendedor'").scalar()
            check("descripción alineada con el código", row == rbac_service.SYSTEM_ROLE_DESCRIPTIONS["vendedor"])
            check("revertir restaura el texto anterior", migration.revert_changes(connection, changes) == 1)
        with engine.begin() as connection:
            check("texto anterior restaurado", connection.exec_driver_sql("SELECT description FROM roles WHERE slug='vendedor'").scalar()
                  == "Ventas, clientes, cotizaciones, pedidos y notas de entrega.")
            check("permisos intactos tras aplicar y revertir",
                  connection.exec_driver_sql("SELECT role_id, permission_id FROM role_permissions ORDER BY 1, 2").fetchall() == links_before)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="settings_fix_") as tmp:
        folder = Path(tmp)
        app, _ = setup(folder)
        app.config["WTF_CSRF_ENABLED"] = True
        test_csrf(app)
        test_mfa_replacement(app)
        test_roles_and_actions(app)
        test_save_reset(app)
        test_backups(app, folder)
        test_role_description_migration(app)
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
    print(f"\n{core.CHECKS_RUN - core.FAILURES}/{core.CHECKS_RUN} comprobaciones OK")
    return 1 if core.FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
