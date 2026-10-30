"""Rutas REST de Configuración.

Lectura: settings:read. Escritura: settings:write. Roles: roles:manage. Respaldos: backups:create /
backups:manage / backups:restore. Sesiones, historial y bandeja de notificaciones son datos propios
del usuario autenticado (login_required). Toda mutación exige X-CSRFToken después del control de acceso.
"""
from functools import wraps
from urllib.parse import parse_qs, urlsplit
from flask import Blueprint, request, jsonify

from app.controllers import settings_controller as c
from app.utils.auth_decorators import login_required, permission_required
from app.utils.csrf_guard import csrf_protected
from app.utils.permissions import (BACKUPS_CREATE, BACKUPS_MANAGE, BACKUPS_RESTORE, NOTIFICATIONS_MANAGE, ROLES_MANAGE,
                                   SETTINGS_READ, SETTINGS_WRITE)

settings_bp = Blueprint("settings", __name__, url_prefix="/api/settings")


@settings_bp.errorhandler(413)
def oversized_request(_error):
    return jsonify(error="El archivo supera el límite de 2 MB."), 413


_read, _write = permission_required(SETTINGS_READ), permission_required(SETTINGS_WRITE)
_roles, _notify = permission_required(ROLES_MANAGE), permission_required(NOTIFICATIONS_MANAGE)
_bk_create, _bk_manage, _bk_restore = (permission_required(BACKUPS_CREATE), permission_required(BACKUPS_MANAGE),
                                       permission_required(BACKUPS_RESTORE))


def _rule(rule, view, methods, guard):
    if any(method in ("POST", "PUT", "DELETE", "PATCH") for method in methods):
        view = _mutation_guard(view)
    settings_bp.add_url_rule(rule, view_func=guard(view), methods=methods, endpoint=view.__name__)


def _mutation_guard(view):
    @wraps(view)
    def guarded(*args, **kwargs):
        if request.args.get("ref") == "1" or parse_qs(urlsplit(request.referrer or "").query).get("ref") == ["1"]:
            return jsonify(error="La vista de referencia no permite cambios."), 403
        if view.__name__ == "update_settings":
            request.max_content_length = 2 * 1024 * 1024 + 64 * 1024
            if request.content_length and request.content_length > request.max_content_length:
                return jsonify(error="El archivo supera el límite de 2 MB."), 413
        # Toda mutación de Configuración (ajustes, roles, notificaciones, respaldos) exige el token de la página.
        return csrf_protected(view)(*args, **kwargs)
    return guarded


_rule("", c.get_settings, ["GET"], _read)
_rule("/company-logo", c.company_logo, ["GET"], login_required)
_rule("/company-logo", c.delete_company_logo, ["DELETE"], _write)
_rule("", c.update_settings, ["PUT"], _write)
_rule("/reset", c.reset_settings, ["POST"], _write)

_rule("/roles", c.list_roles, ["GET"], _read)
_rule("/roles", c.create_role, ["POST"], _roles)
_rule("/roles/<int:role_id>", c.get_role, ["GET"], _read)
_rule("/roles/<int:role_id>", c.update_role, ["PUT"], _roles)
_rule("/roles/<int:role_id>", c.delete_role, ["DELETE"], _roles)
_rule("/roles/<int:role_id>/duplicate", c.duplicate_role, ["POST"], _roles)

_rule("/security", c.security_overview, ["GET"], login_required)
_rule("/security/sessions", c.list_sessions, ["GET"], login_required)
_rule("/security/sessions/revoke", c.revoke_sessions, ["POST"], login_required)
_rule("/security/sessions/<int:session_id>", c.revoke_session, ["DELETE"], login_required)
_rule("/security/login-history", c.login_history, ["GET"], login_required)

_rule("/notifications/overview", c.notification_overview, ["GET"], _read)
_rule("/notifications", c.list_notifications, ["GET"], login_required)
_rule("/notifications/read-all", c.read_all_notifications, ["POST"], login_required)
_rule("/notifications/test", c.test_notifications, ["POST"], _notify)
_rule("/notifications/<int:notification_id>", c.notification_detail, ["GET"], login_required)
_rule("/notifications/<int:notification_id>/read", c.read_notification, ["POST"], login_required)

_rule("/backups", c.list_backups, ["GET"], _read)
_rule("/backups", c.create_backup, ["POST"], _bk_create)
_rule("/backups/schedule/apply", c.apply_schedule, ["POST"], _bk_manage)
_rule("/backups/drive/connect", c.drive_connect, ["GET"], _bk_manage)
_rule("/backups/drive/callback", c.drive_callback, ["GET"], _bk_manage)
_rule("/backups/drive", c.drive_disconnect, ["DELETE"], _bk_manage)
_rule("/backups/<int:backup_id>", c.inspect_backup, ["GET"], _read)
_rule("/backups/<int:backup_id>", c.delete_backup, ["DELETE"], _bk_manage)
_rule("/backups/<int:backup_id>/download", c.download_backup, ["GET"], _bk_manage)
_rule("/backups/<int:backup_id>/verify", c.verify_backup, ["POST"], _bk_manage)
_rule("/backups/<int:backup_id>/drive-upload", c.drive_upload, ["POST"], _bk_manage)
_rule("/backups/<int:backup_id>/restore-preview", c.restore_preview, ["GET"], _bk_restore)
_rule("/backups/<int:backup_id>/restore", c.restore_backup, ["POST"], _bk_restore)
