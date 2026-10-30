#!/usr/bin/env python3
"""Suite aislada de Settings completo: SQLite temporal, sin MySQL, sin ferreteria_conejo, sin red.

Reutiliza la app aislada de test_settings_core.py y cubre lo que ese archivo no toca:
RBAC dinámico (roles, matriz, bloqueo del Administrador, permisos por rol), política de contraseña
con historial, registro de sesiones (revocación inmediata), historial de accesos, 2FA TOTP real
(enrolamiento, anti-replay por paso, códigos de recuperación de un solo uso, dispositivo confiable
con etiqueta, re-autenticación fuerte), notificaciones (canales honestos, matriz validada, prueba
en modo dry-run, bandeja), respaldos (tipo inválido, carpeta con '..', confirmación RESTAURAR),
restablecer por pestaña y aislamiento del modo referencia (?ref=1 no escribe ninguna tabla).

No crea respaldos de archivos (empaquetarían uploads/ e instance/ reales). Sin correo externo:
NOTIFICATIONS_DRY_RUN=True.

Uso: python scripts/test_settings_full.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pyotp  # noqa: E402
from sqlalchemy import text  # noqa: E402

import test_settings_core as core  # noqa: E402  (app aislada, login, check)
from app.extensions import db  # noqa: E402
from app.services import settings_service as s  # noqa: E402

check, login, ADMIN = core.check, core.login, core.ADMIN_PASSWORD
COUNTED_TABLES = ("users", "roles", "role_permissions", "app_settings", "user_sessions", "login_events", "password_history",
                  "user_mfa", "mfa_recovery_codes", "trusted_devices", "notifications", "notification_deliveries", "backups",
                  "restore_runs", "integration_connections")


def counts(app) -> dict:
    with app.app_context():
        return {t: db.session.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in COUNTED_TABLES}


def test_rbac(app) -> None:
    inv = app.test_client()
    login(inv, "core_inv", "inv123")
    check("inventario: POST /roles -> 403", inv.post("/api/settings/roles", json={"name": "x", "permissions": ["products:read"]}).status_code == 403)
    admin = app.test_client()
    login(admin, "core_admin", ADMIN)
    roles = admin.get("/api/settings/roles").get_json()
    check("roles: 3 del sistema sembrados y catálogo de permisos", roles["available"] and len(roles["roles"]) == 3
          and all(r["is_system"] for r in roles["roles"]) and len(roles["permissions"]) > 0)
    admin_role = next(r for r in roles["roles"] if r["slug"] == "admin")
    check("roles: conteo real de usuarios por rol", admin_role["user_count"] == 1)
    r = admin.post("/api/settings/roles", json={"name": "Auditor", "description": "Solo lectura", "permissions": ["products:read", "sales:read"]})
    check("roles: crear -> 201 con permisos", r.status_code == 201 and sorted(r.get_json()["role"]["permissions"]) == ["products:read", "sales:read"])
    role_id = r.get_json()["role"]["id"]
    check("roles: nombre duplicado -> 409", admin.post("/api/settings/roles", json={"name": "auditor", "permissions": ["products:read"]}).status_code == 409)
    check("roles: sin permisos -> 400", admin.post("/api/settings/roles", json={"name": "Vacío", "permissions": []}).status_code == 400)
    check("roles: permiso desconocido -> 400", admin.post("/api/settings/roles", json={"name": "Raro", "permissions": ["x:y"]}).status_code == 400)
    r = admin.put(f"/api/settings/roles/{role_id}", json={"permissions": ["products:read", "inventory:read"]})
    check("roles: matriz guardada y releída", r.status_code == 200
          and sorted(admin.get(f"/api/settings/roles/{role_id}").get_json()["permissions"]) == ["inventory:read", "products:read"])
    check("roles: Administrador conserva permisos críticos -> 409",
          admin.put(f"/api/settings/roles/{admin_role['id']}", json={"permissions": ["products:read"]}).status_code == 409)
    check("roles: Administrador no se desactiva -> 409", admin.put(f"/api/settings/roles/{admin_role['id']}", json={"is_active": False}).status_code == 409)
    check("roles: sistema no se elimina -> 409", admin.delete(f"/api/settings/roles/{admin_role['id']}").status_code == 409)
    r = admin.post(f"/api/settings/roles/{role_id}/duplicate")
    check("roles: duplicar copia permisos", r.status_code == 201 and r.get_json()["role"]["name"].startswith("Copia de")
          and sorted(r.get_json()["role"]["permissions"]) == ["inventory:read", "products:read"])
    copy_id = r.get_json()["role"]["id"]
    r = admin.post("/api/users", json={"name": "Auditor Uno", "email": "auditor@core.test", "username": "core_aud",
                                       "password": "Auditor123", "role": "auditor"})
    check("usuarios: alta con rol dinámico", r.status_code == 201, r.get_data(as_text=True))
    check("roles: con usuarios asignados no se elimina -> 409", admin.delete(f"/api/settings/roles/{role_id}").status_code == 409)
    aud = app.test_client()
    login(aud, "core_aud", "Auditor123")
    check("rol dinámico: permiso concedido funciona", aud.get("/api/products").status_code == 200)
    check("rol dinámico: permiso ausente -> 403", aud.get("/api/settings").status_code == 403)
    admin.put(f"/api/settings/roles/{role_id}", json={"is_active": False})
    check("roles: rol desactivado no se asigna -> 400", admin.post("/api/users", json={
        "name": "Dos", "email": "dos@core.test", "username": "core_dos", "password": "Auditor123", "role": "auditor"}).status_code == 400)
    check("roles: copia sin usuarios se elimina", admin.delete(f"/api/settings/roles/{copy_id}").status_code == 200
          and all(r["id"] != copy_id for r in admin.get("/api/settings/roles").get_json()["roles"]))


def test_security(app) -> None:
    admin = app.test_client()
    login(admin, "core_admin", ADMIN)
    r = admin.put("/api/settings", json={s.PASSWORD_MIN_LENGTH: 10, s.PASSWORD_REQUIRE_UPPERCASE: True,
                                         s.PASSWORD_REQUIRE_NUMBER: True, s.PASSWORD_HISTORY_COUNT: 2})
    check("política: guardada", r.status_code == 200 and r.get_json()["settings"][s.PASSWORD_MIN_LENGTH] == 10)
    check("política: expiración negativa -> 400", admin.put("/api/settings", json={s.PASSWORD_EXPIRATION_DAYS: -1}).status_code == 400)
    change = lambda cur, new: admin.post("/api/auth/change-password", json={"current_password": cur, "new_password": new, "confirm_password": new})  # noqa: E731
    check("política: sin mayúscula -> 400", change(ADMIN, "solominusculas1").status_code == 400)
    check("política: sin número -> 400", change(ADMIN, "SinNumerosAqui").status_code == 400)
    new_password = "ClaveNueva2026"
    check("política: cumple -> 200", change(ADMIN, new_password).status_code == 200)
    check("historial: no reutiliza la anterior -> 400", change(new_password, ADMIN).status_code == 400)
    with app.app_context():
        rows = db.session.execute(text("SELECT password_hash FROM password_history")).scalars().all()
        check("historial: solo hashes", len(rows) >= 1 and all(h.startswith(("scrypt:", "pbkdf2:")) and ADMIN not in h for h in rows))
    # Sesiones: dos clientes = dos sesiones; revocar la otra la rechaza al instante
    other = app.test_client()
    other.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 (Linux; Android 14) Chrome/128 Mobile"
    login(other, "core_admin", new_password)
    sessions = admin.get("/api/settings/security/sessions").get_json()
    check("sesiones: registradas ambas con marca de actual", len(sessions["items"]) >= 2 and sum(1 for i in sessions["items"] if i["current"]) == 1
          and all(i["location"] == "No disponible" for i in sessions["items"]))
    target = next(i for i in sessions["items"] if not i["current"])
    check("sesiones: revocar una -> otra sesión 401", admin.delete(f"/api/settings/security/sessions/{target['id']}").status_code == 200
          and other.get("/api/auth/me").status_code == 401)
    login(other, "core_admin", new_password)
    check("sesiones: scope inválido -> 400", admin.post("/api/settings/security/sessions/revoke", json={"scope": "x"}).status_code == 400)
    check("sesiones: cerrar las demás", admin.post("/api/settings/security/sessions/revoke", json={"scope": "others"}).get_json()["closed"] >= 1
          and other.get("/api/auth/me").status_code == 401 and admin.get("/api/auth/me").status_code == 200)
    bad = app.test_client().post("/api/auth/login", json={"identifier": "core_admin", "password": "incorrecta"})
    history = admin.get("/api/settings/security/login-history?per_page=50").get_json()
    check("historial de accesos: éxito y fallo registrados", bad.status_code == 401 and any(i["success"] for i in history["items"])
          and any(not i["success"] for i in history["items"]))
    # 2FA TOTP real
    enroll = admin.post("/api/auth/mfa/enroll").get_json()
    check("2FA: QR SVG + clave manual", enroll["qr_svg"].lstrip().startswith("<svg") and len(enroll["setup_key"]) >= 16)
    check("2FA: aún no habilitado", admin.get("/api/auth/mfa").get_json()["enabled"] is False)
    totp = pyotp.TOTP(enroll["setup_key"])
    check("2FA: código inválido -> 400", admin.post("/api/auth/mfa/confirm", json={"code": "000000"}).status_code == 400)
    now = time.time()
    confirm = admin.post("/api/auth/mfa/confirm", json={"code": totp.at(now)})
    codes = confirm.get_json().get("recovery_codes") or []
    check("2FA: confirmado con 10 códigos de recuperación", confirm.status_code == 200 and len(codes) == 10)
    with app.app_context():
        raw = db.session.execute(text("SELECT secret_encrypted FROM user_mfa")).scalar()
        stored = db.session.execute(text("SELECT code_hash FROM mfa_recovery_codes")).scalars().all()
        check("2FA: secreto cifrado y códigos solo hash", enroll["setup_key"] not in (raw or "") and not (set(codes) & set(stored)))
    check("2FA: estado no expone el secreto", "setup_key" not in admin.get("/api/auth/mfa").get_json())
    check("2FA: anti-replay, mismo paso rechazado", admin.post("/api/auth/mfa/devices", json={"code": totp.at(now)}).status_code == 400)
    r = admin.post("/api/auth/mfa/devices", json={"code": totp.at(now + 30), "label": "Portátil de pruebas"})
    check("2FA: paso siguiente aceptado y dispositivo con etiqueta", r.status_code == 200
          and r.get_json()["items"][0]["label"] == "Portátil de pruebas" and "fc_trusted_device" in (r.headers.get("Set-Cookie") or ""))
    fresh = app.test_client()
    r = fresh.post("/api/auth/login", json={"identifier": "core_admin", "password": new_password})
    check("2FA: login exige desafío sin abrir sesión", r.status_code == 200 and r.get_json()["mfa_required"] is True
          and fresh.get("/api/auth/me").status_code == 401)
    check("2FA: verify incorrecto -> 400", fresh.post("/api/auth/mfa/verify", json={"code": "123456"}).status_code == 400)
    check("2FA: código de recuperación abre sesión", fresh.post("/api/auth/mfa/verify", json={"code": codes[0]}).status_code == 200
          and fresh.get("/api/auth/me").status_code == 200)
    again = app.test_client()
    again.post("/api/auth/login", json={"identifier": "core_admin", "password": new_password})
    check("2FA: código de recuperación de un solo uso", again.post("/api/auth/mfa/verify", json={"code": codes[0]}).status_code == 400)
    check("2FA: regenerar exige contraseña -> 400",
          admin.post("/api/auth/mfa/recovery-codes", json={"code": codes[1], "current_password": "mala"}).status_code == 400)
    check("2FA: desactivar con re-autenticación fuerte", admin.post("/api/auth/mfa/disable", json={"code": codes[2], "current_password": new_password}).status_code == 200
          and admin.get("/api/auth/mfa").get_json()["enabled"] is False and admin.get("/api/auth/mfa/devices").get_json()["items"] == [])
    r = admin.post("/api/settings/reset", json={"tab": "security"})
    check("reset seguridad: política por defecto sin tocar credenciales", r.status_code == 200
          and r.get_json()["settings"][s.PASSWORD_MIN_LENGTH] == 6
          and app.test_client().post("/api/auth/login", json={"identifier": "core_admin", "password": new_password}).status_code == 200)
    admin.put("/api/settings", json={s.PASSWORD_HISTORY_COUNT: 0})
    check("restaurar clave de pruebas", change(new_password, ADMIN).status_code == 200)


def test_notifications(app) -> None:
    admin = app.test_client()
    login(admin, "core_admin", ADMIN)
    overview = admin.get("/api/settings/notifications/overview").get_json()
    check("canales: app primero y siempre disponible; externos con variables faltantes",
          [c["channel"] for c in overview["channels"]] == ["app", "email", "whatsapp", "sms"] and overview["channels"][0]["configured"]
          and all(not c["configured"] and c["missing"] for c in overview["channels"][1:]))
    check("canales: sin secretos en la respuesta", "TOKEN" not in str(overview).upper().replace("WHATSAPP_ACCESS_TOKEN", "").replace("SMS_AUTH_TOKEN", ""))
    check("canal externo sin proveedor -> 400", admin.put("/api/settings", json={s.EMAIL_NOTIFICATIONS: True}).status_code == 400)
    check("celda externa con canal apagado -> 400", admin.put("/api/settings", json={s.matrix_key("sales", "email"): True}).status_code == 400)
    check("correo destino inválido -> 400", admin.put("/api/settings", json={s.EMAIL_TO: "no-es-correo"}).status_code == 400)
    check("frecuencia inválida -> 400", admin.put("/api/settings", json={s.DIGEST_FREQUENCY: "hourly"}).status_code == 400)
    r = admin.put("/api/settings", json={s.EMAIL_TO: "alertas@example.invalid", s.DIGEST_FREQUENCY: "daily", s.DIGEST_TIME: "07:30",
                                         s.matrix_key("sales", "app"): False})
    check("correo + celda app guardadas", r.status_code == 200 and r.get_json()["settings"][s.DIGEST_TIME] == "07:30"
          and r.get_json()["settings"][s.matrix_key("sales", "app")] is False)
    r = admin.post("/api/settings/notifications/test", json={"channels": ["app", "email"]})
    results = {x["channel"]: x for x in r.get_json()["results"]}
    check("probar: app real OK, correo honesto (desactivado/no configurado)", r.status_code == 200 and results["app"]["ok"] is True
          and results["email"]["ok"] is False)
    inbox = admin.get("/api/settings/notifications?per_page=5").get_json()
    check("bandeja: notificación de prueba listada sin leer", inbox["total"] >= 1 and inbox["unread"] >= 1)
    first = inbox["items"][0]["id"]
    detail = admin.get(f"/api/settings/notifications/{first}").get_json()
    check("bandeja: detalle con canal app entregado", "app" in detail["channels"] and detail["deliveries"] == [])
    check("bandeja: marcar todas", admin.post("/api/settings/notifications/read-all").status_code == 200
          and admin.get("/api/settings/notifications?per_page=1").get_json()["unread"] == 0)
    other = app.test_client()
    login(other, "core_inv", "inv123")
    check("bandeja: privada por usuario -> 404 ajena", other.get(f"/api/settings/notifications/{first}").status_code == 404)
    check("probar: sin permiso -> 403", other.post("/api/settings/notifications/test", json={"channels": ["app"]}).status_code == 403)
    shell = admin.get("/api/shell/notifications").get_json()
    check("campana: bandeja real disponible", shell["available"] is True and shell["unread"] == 0)
    r = admin.post("/api/settings/reset", json={"tab": "notifications"})
    check("reset notificaciones: valores por defecto", r.status_code == 200 and r.get_json()["settings"][s.EMAIL_TO] == "")


def test_backups(app) -> None:
    admin = app.test_client()
    login(admin, "core_admin", ADMIN)
    check("respaldo: tipo inválido -> 400", admin.post("/api/settings/backups", json={"kind": "tar"}).status_code == 400)
    check("respaldo: carpeta con '..' -> 400", admin.put("/api/settings", json={s.BACKUP_DIRECTORY: "..\\..\\Windows"}).status_code == 400)
    check("respaldo: carpeta relativa -> 400", admin.put("/api/settings", json={s.BACKUP_DIRECTORY: "backups"}).status_code == 400)
    check("respaldo: frecuencia inválida -> 400", admin.put("/api/settings", json={s.BACKUP_FREQUENCY: "hourly"}).status_code == 400)
    check("respaldo: Drive sin proveedor -> 400 al elegir local_drive", admin.put("/api/settings", json={s.BACKUP_LOCATION: "local_drive"}).status_code == 400)
    r = admin.get("/api/settings/backups/drive/connect")
    check("respaldo: conectar Drive sin credenciales -> 503 con variables faltantes (sin secretos)", r.status_code == 503
          and "GOOGLE_DRIVE_CLIENT_ID" in r.get_json()["error"])
    check("restaurar: inexistente -> 404", admin.post("/api/settings/backups/999/restore", json={"confirmation": "RESTAURAR"}).status_code == 404)
    status = admin.get("/api/settings/backups").get_json()
    check("respaldo: estado honesto (sin mysqldump, sin Drive, sin tarea)", status["mysqldump_configured"] is False
          and status["drive"]["connected"] is False and status["schedule"]["task"]["next_run"] is None
          and status["schedule"]["task"]["registered"] is False)
    inv = app.test_client()
    login(inv, "core_inv", "inv123")
    check("respaldo: inventario sin backups:manage -> 403", inv.post("/api/settings/backups/schedule/apply").status_code == 403)
    r = admin.post("/api/settings/reset", json={"tab": "backup"})
    check("reset respaldo: defaults", r.status_code == 200 and r.get_json()["settings"][s.BACKUP_FREQUENCY] == "off")
    check("reset: pestaña desconocida -> 400", admin.post("/api/settings/reset", json={"tab": "x"}).status_code == 400)


def test_reference_mode(app) -> None:
    admin = app.test_client()
    login(admin, "core_admin", ADMIN)
    before = counts(app)
    for tab in core.TABS:
        check(f"referencia {tab}: 200", admin.get(f"/settings?tab={tab}&ref=1").status_code == 200)
    check("referencia: cero escrituras en todas las tablas", counts(app) == before)


def main() -> int:
    core.IsolatedConfig.BACKUP_TASK_NAME = "FerreteriaConejo-RespaldoAutomatico-TestAislado"  # jamás la tarea real
    with tempfile.TemporaryDirectory(prefix="settings_full_") as tmp:
        app, _ids = core.setup_app(Path(tmp))
        try:
            test_rbac(app)
            test_security(app)
            test_notifications(app)
            test_backups(app)
            test_reference_mode(app)
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
    print(f"\n{core.CHECKS_RUN - core.FAILURES}/{core.CHECKS_RUN} comprobaciones OK")
    return 1 if core.FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
