"""Controlador de Configuración: API JSON (permisos settings:*/roles/backups) y contexto real de la página en vivo."""
import shutil
import json

from flask import current_app, jsonify, redirect, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models.backup import BACKUP_KIND_LABELS, BACKUP_KINDS
from app.models.notification import CHANNELS, EVENT_LABELS, EVENT_SALES, EVENTS
from app.services import (backup_service, google_drive_service, login_event_service, mfa_service, notification_providers,
                          notification_service, password_policy_service, rbac_service, scheduler_service, session_service,
                          settings_service)
from app.services.exceptions import ValidationError
from app.utils.permissions import MODULE_LABELS, WITHDRAWN_CUSTOMER_SALES

ROLE_TONES = {"admin": ("shield", "purple"), "inventario": ("boxes", "blue"), "vendedor": ("cart", "green")}
_DEFAULT_TONE = ("users", "blue")
# módulo del permiso (prefijo) -> icono, tono (para la matriz de permisos).
_MODULE_ICONS = {
    "products": ("boxes", "blue"), "categories": ("boxes", "blue"), "inventory": ("boxes", "blue"),
    "delivery_notes": ("truck", "green"), "reports": ("chart", "pink"), "users": ("users", "purple"),
    "roles": ("shield", "purple"), "historical_imports": ("clipboard", "orange"), "predictions": ("chart", "purple"),
    "customers": ("users", "green"), "sales": ("cart", "green"), "settings": ("settings", "blue"),
    "security": ("lock", "purple"), "notifications": ("bell", "orange"), "backups": ("database", "blue"),
}


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("El cuerpo de la petición debe ser un JSON válido.")
    return data


def _int_arg(name: str, default: int) -> int:
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        raise ValidationError(f"El parámetro '{name}' debe ser un entero.")


# ---- API: configuración ----
def get_settings():
    return jsonify({"settings": settings_service.get_all(), "available": settings_service.is_available()})


def update_settings():
    if request.mimetype == "multipart/form-data":
        try:
            values = json.loads(request.form.get("settings", ""))
        except (ValueError, TypeError):
            raise ValidationError("La configuración no es válida.")
        settings = settings_service.update(values, current_user, logo=request.files.get("logo"),
                                           delete_logo=request.form.get("delete_logo") == "1")
    else:
        settings = settings_service.update(_json_body(), current_user)
    return jsonify({"message": "Configuración guardada.", "settings": settings})


def company_logo():
    from app.services.company_logo_service import get_company_logo_path
    from flask import abort

    path = get_company_logo_path()
    if path is None:
        abort(404)
    response = send_file(path, mimetype="image/webp", max_age=0)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-cache"
    return response


def delete_company_logo():
    settings = settings_service.update({}, current_user, delete_logo=True)
    return jsonify(message="Logo eliminado.", settings=settings)


def reset_settings():
    tab = _json_body().get("tab")
    return jsonify({"message": "Valores restablecidos.", "settings": settings_service.reset_tab(tab)})


# ---- API: roles y permisos ----
def permission_catalog() -> list[dict]:
    """Permisos agrupados por módulo para la matriz (etiquetas en español).

    En la DEMO no se ofrecen los permisos comerciales retirados: el servicio los rechaza al guardar
    (_clean_permissions) y presentarlos permitiría preparar lotes imposibles, incluso con "Seleccionar todo".
    """
    withdrawn = WITHDRAWN_CUSTOMER_SALES if rbac_service.customer_sales_withdrawn() else frozenset()
    groups: dict[str, dict] = {}
    for permission in rbac_service.list_permissions():
        if permission["code"] in withdrawn:
            continue
        module = permission["module"]
        icon, tone = _MODULE_ICONS.get(module, _DEFAULT_TONE)
        group = groups.setdefault(module, {"module": module, "label": MODULE_LABELS.get(module, module), "symbol": icon,
                                           "tone": tone, "permissions": []})
        group["permissions"].append(permission)
    return list(groups.values())


def list_roles():
    return jsonify({"roles": rbac_service.list_roles(), "permissions": permission_catalog(),
                    "available": rbac_service.is_available()})


def create_role():
    role = rbac_service.create_role(_json_body(), current_user)
    return jsonify({"message": "Rol creado.", "role": role.to_dict(0)}), 201


def get_role(role_id: int):
    role = rbac_service.get_role_or_404(role_id)
    return jsonify(next(r for r in rbac_service.list_roles() if r["id"] == role.id))


def update_role(role_id: int):
    role = rbac_service.update_role(role_id, _json_body(), current_user)
    return jsonify({"message": "Rol actualizado.", "role": next(r for r in rbac_service.list_roles() if r["id"] == role.id)})


def duplicate_role(role_id: int):
    role = rbac_service.duplicate_role(role_id, current_user)
    return jsonify({"message": "Rol duplicado.", "role": role.to_dict(0)}), 201


def delete_role(role_id: int):
    rbac_service.delete_role(role_id, current_user)
    return jsonify({"message": "Rol eliminado."})


# ---- API: seguridad (datos propios) ----
def security_overview():
    return jsonify({"policy": password_policy_service.policy(), "sessions": session_service.list_sessions(current_user),
                    "mfa": mfa_service.status(current_user),
                    "history": login_event_service.list_events(current_user, page=1, per_page=5),
                    "password_expires_at": (password_policy_service.expires_at(current_user).isoformat()
                                            if password_policy_service.expires_at(current_user) else None),
                    "has_password": bool(current_user.password_hash)})


def list_sessions():
    return jsonify(session_service.list_sessions(current_user))


def revoke_session(session_id: int):
    return jsonify({"message": "Sesión cerrada.", **session_service.revoke(current_user, session_id)})


def revoke_sessions():
    scope = _json_body().get("scope")
    actions = {"others": session_service.revoke_others, "all": session_service.revoke_all,
               "inactive": session_service.revoke_inactive}
    if scope not in actions:
        raise ValidationError("Indique scope: others, all o inactive.")
    result = actions[scope](current_user)
    return jsonify({"message": f"Sesiones cerradas: {result['closed']}.", **result})


def login_history():
    return jsonify(login_event_service.list_events(current_user, page=_int_arg("page", 1), per_page=_int_arg("per_page", 10)))


# ---- API: notificaciones ----
def configurable_events() -> tuple[str, ...]:
    """Eventos con fila en la matriz. Las ventas/pedidos retirados de la DEMO no se ofrecen como configurables;
    sus claves y avisos históricos se conservan intactos."""
    return tuple(e for e in EVENTS if not (e == EVENT_SALES and rbac_service.customer_sales_withdrawn()))


def notification_overview():
    settings = settings_service.get_all()
    events = configurable_events()
    return jsonify({"channels": notification_providers.status_all(settings),
                    "events": [{"key": e, "label": EVENT_LABELS[e]} for e in events], "channel_keys": list(CHANNELS),
                    "matrix": {e: {c: settings[settings_service.matrix_key(e, c)] for c in CHANNELS} for e in events}})


def list_notifications():
    return jsonify(notification_service.list_for_user(current_user, page=_int_arg("page", 1),
                                                      per_page=_int_arg("per_page", 6), event=request.args.get("event") or None))


def notification_detail(notification_id: int):
    return jsonify(notification_service.detail(current_user, notification_id))


def read_notification(notification_id: int):
    row = notification_service.mark_read(current_user, notification_id)
    return jsonify({"notification": row.to_dict(), "unread": notification_service.unread_count(current_user)})


def read_all_notifications():
    return jsonify({"marked": notification_service.mark_all_read(current_user)})


def test_notifications():
    results = notification_service.send_test(current_user, _json_body().get("channels"))
    return jsonify({"results": results, "ok": all(r["ok"] for r in results)})


# ---- API: respaldos ----
def list_backups():
    return jsonify(backup_service.status())


def create_backup():
    kind = (request.get_json(silent=True) or {}).get("kind", "database")  # sin cuerpo => solo base de datos
    record = backup_service.create_backup(current_user, kind=kind)
    return jsonify({"message": f"Respaldo '{BACKUP_KIND_LABELS.get(kind, kind)}' creado.", "backup": record.to_dict()}), 201


def inspect_backup(backup_id: int):
    return jsonify(backup_service.inspect_backup(backup_service.get_backup_or_404(backup_id)))


def download_backup(backup_id: int):
    record = backup_service.get_backup_or_404(backup_id)
    return send_file(backup_service.download_path(record), as_attachment=True, download_name=record.filename)


def verify_backup(backup_id: int):
    record = backup_service.get_backup_or_404(backup_id)
    return jsonify({**backup_service.verify(record), "backup": record.to_dict()})


def delete_backup(backup_id: int):
    record = backup_service.get_backup_or_404(backup_id)
    backup_service.delete_backup(record)
    return jsonify({"message": "Respaldo eliminado.", "backup": record.to_dict()})


def restore_preview(backup_id: int):
    return jsonify(backup_service.restore_preview(backup_service.get_backup_or_404(backup_id)))


def restore_backup(backup_id: int):
    record = backup_service.get_backup_or_404(backup_id)
    run = backup_service.restore(record, current_user, _json_body())
    notification_service.maintenance_event("Restauración ejecutada", f"{record.filename}: {run.detail}", severity="warning")
    return jsonify({"message": "Restauración completada.", "restore": run.to_dict()})


def apply_schedule():
    result = scheduler_service.apply_schedule()
    messages = {"created": "Tarea programada registrada.", "removed": "Tarea programada eliminada.",
                "absent": "No había tarea programada que eliminar."}
    return jsonify({"message": messages[result["action"]], **result})


def drive_connect():
    return google_drive_service.authorize_redirect()


def drive_callback():
    google_drive_service.handle_callback(current_user)
    return redirect(url_for("pages.settings", tab="backup", drive="connected"))


def drive_disconnect():
    google_drive_service.disconnect()
    return jsonify({"message": "Google Drive desconectado.", "drive": google_drive_service.status()})


def drive_upload(backup_id: int):
    record = google_drive_service.upload_backup(backup_service.get_backup_or_404(backup_id))
    return jsonify({"message": "Respaldo subido a Google Drive.", "backup": record.to_dict()})


# ---- Contexto de la página en vivo ----
def _database_info() -> dict:
    try:
        db.session.execute(text("SELECT 1"))
    except SQLAlchemyError:  # estado honesto: la página sigue mostrándose con "Error de conexión"
        db.session.rollback()
        return {"connected": False, "engine": None}
    dialect = db.engine.dialect
    version = ".".join(str(part) for part in dialect.server_version_info or ())
    return {"connected": True, "engine": f"{dialect.name} {version}".strip()}


def _disk_usage() -> dict:
    usage = shutil.disk_usage(current_app.root_path)
    return {"used": usage.used, "total": usage.total, "percent": round(usage.used * 100 / usage.total) if usage.total else 0}


def page_context(tab: str) -> dict:
    """Datos reales por pestaña; nunca valores de maqueta."""
    settings = settings_service.get_all()
    context = {"settings": settings, "settings_available": settings_service.is_available(),
               "app_version": current_app.config.get("APP_VERSION", "—")}
    if tab == "general":
        context["database"] = _database_info()
        context["disk"] = _disk_usage()
    elif tab == "notifications":
        context["channels"] = notification_providers.status_all(settings)
        context["events"] = [{"key": e, "label": EVENT_LABELS[e]} for e in configurable_events()]
        context["matrix_key"] = settings_service.matrix_key
        context["notifications"] = notification_service.list_for_user(current_user, page=1, per_page=4)
    elif tab == "users":
        roles = rbac_service.list_roles()
        for role in roles:
            role["symbol"], role["tone"] = ROLE_TONES.get(role["slug"], _DEFAULT_TONE)
        context["roles"] = roles
        context["permission_groups"] = permission_catalog()
        context["roles_available"] = rbac_service.is_available()
        context["customer_sales_withdrawn"] = rbac_service.customer_sales_withdrawn()
    elif tab == "security":
        context["security"] = security_overview().get_json()
    elif tab == "backup":
        context["backup"] = backup_service.status()
        context["backup_kinds"] = [(k, BACKUP_KIND_LABELS[k]) for k in BACKUP_KINDS]
    elif tab == "predictive":
        context["predictive"] = _predictive_capabilities()
    elif tab == "integrations":
        context["integrations"] = _integration_status(settings)
    return context


def _predictive_capabilities() -> dict:
    """Parámetros fijos del código, de solo lectura. No calcula pronósticos ni entrena modelos."""
    from app.services import forecast_service as f, prediction_readiness_service as r

    return {"horizons": f.HORIZONS_DAYS, "default_horizon": f.DEFAULT_HORIZON_DAYS,
            "models": [f.MODEL_LABELS[key] for key in f.MODELS], "baseline": f.MODEL_LABELS[f.BASELINE_MODEL],
            "z_80": f.Z_80,
            "thresholds": [(r.CLASS_LABELS[r.CLASS_ADVANCED_READY], r.ADVANCED_MIN_PERIODS, r.ADVANCED_MIN_POSITIVE),
                           (r.CLASS_LABELS[r.CLASS_SIMPLE_READY], r.SIMPLE_MIN_PERIODS, r.SIMPLE_MIN_POSITIVE),
                           (r.CLASS_LABELS[r.CLASS_LIMITED], r.LIMITED_MIN_PERIODS, r.LIMITED_MIN_POSITIVE)]}


def _integration_status(settings: dict) -> list[dict]:
    """Proveedores existentes en el código: solo si están configurados y los nombres de variables faltantes."""
    rows = [{"name": c["label"], "used_in": "notifications", "missing": c["missing"]}
            for c in notification_providers.status_all(settings) if c["channel"] != "app"]
    rows.append({"name": "Google Drive (copia de respaldos)", "used_in": "backup",
                 "missing": google_drive_service.status()["missing"]})
    oidc = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI")
    rows.append({"name": "Google (inicio de sesión)", "used_in": "login",
                 "missing": [name for name in oidc if not current_app.config.get(name)]})
    return rows
