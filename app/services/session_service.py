"""Registro de sesiones en el servidor: cada login crea una fila (solo hash del token) y toda
petición autenticada la valida; revocar una sesión la invalida de inmediato.

Sin la tabla (migración pendiente) el comportamiento es el de Flask-Login puro.
"""
import hashlib
import secrets
from datetime import datetime, timedelta

from flask import current_app, g, jsonify, redirect, request, session, url_for
from flask_login import current_user, login_user, logout_user

from app.extensions import db
from app.models import User, UserSession
from app.services import login_event_service
from app.services.exceptions import NotFoundError, ValidationError
from app.utils.schema import table_ready
from app.utils.profile_request_guard import is_reference_request

SESSION_KEY = "sid"
AUTH_AT_KEY = "auth_at"
INACTIVE_AFTER_HOURS = 24
RECENT_AUTH_MINUTES = 15
_TOUCH_EVERY_SECONDS = 60
# Rutas permitidas con contraseña expirada (cambiarla, cerrar sesión, ver quién soy).
_EXPIRED_ALLOWED_PREFIXES = ("/api/auth/change-password", "/api/auth/logout", "/api/auth/me", "/password-expired",
                             "/login", "/static/", "/api/auth/mfa/")


def is_available() -> bool:
    return table_ready(UserSession.__tablename__)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def open_session(user: User, provider: str = "local") -> None:
    """Inicia sesión Flask-Login, registra la sesión en servidor y actualiza last_login_at."""
    login_user(user)
    now = datetime.utcnow()
    session[AUTH_AT_KEY] = now.timestamp()
    user.last_login_at = now
    if is_available():
        token = secrets.token_urlsafe(32)
        ip, agent = login_event_service.request_meta()
        record = UserSession(user_id=user.id, token_hash=_hash(token), ip_address=ip, user_agent=agent,
                             device_label=login_event_service.device_label(agent), provider=provider,
                             expires_at=now + timedelta(days=current_app.config.get("SESSION_MAX_DAYS", 30)))
        db.session.add(record)
        session[SESSION_KEY] = token
    db.session.commit()


def current_record() -> UserSession | None:
    token = session.get(SESSION_KEY)
    if not token or not is_available():
        return None
    return UserSession.query.filter_by(token_hash=_hash(token)).first()


def recently_authenticated() -> bool:
    at = session.get(AUTH_AT_KEY)
    return bool(at) and datetime.utcnow() - datetime.utcfromtimestamp(at) <= timedelta(minutes=RECENT_AUTH_MINUTES)


def close_current() -> None:
    record = current_record()
    if record is not None and record.revoked_at is None:
        record.revoked_at = datetime.utcnow()
        db.session.commit()
    logout_user()
    session.clear()


def _reject(message: str):
    logout_user()
    session.clear()
    if request.path.startswith("/api/") or request.path.startswith("/media/"):
        return jsonify({"error": message}), 401
    return redirect(url_for("pages.login"))


def _validate_request():
    """before_request: sesión revocada/expirada => 401 inmediato; contraseña expirada => bloqueo parcial."""
    if request.endpoint == "static" or not current_user.is_authenticated:
        return None
    if is_available():
        record = current_record()
        if record is None or not record.is_valid:
            return _reject("La sesión fue cerrada. Inicie sesión nuevamente.")
        g.current_session_id = record.id
        # Preview validates session/security but never refreshes persisted activity.
        preview = is_reference_request()
        if not preview and (datetime.utcnow() - record.last_seen_at).total_seconds() > _TOUCH_EVERY_SECONDS:
            record.last_seen_at = datetime.utcnow()
            db.session.commit()
    return _enforce_password_expiration()


def _enforce_password_expiration():
    from app.services import password_policy_service

    if not password_policy_service.is_expired(current_user):
        return None
    if request.path.startswith(_EXPIRED_ALLOWED_PREFIXES):
        return None
    if request.path.startswith("/api/") or request.path.startswith("/media/"):
        return jsonify({"error": "Su contraseña expiró. Debe cambiarla para continuar.", "password_expired": True}), 403
    return redirect(url_for("pages.password_expired"))


def init_app(app) -> None:
    app.before_request(_validate_request)


# ---- gestión desde Configuración > Seguridad (solo sesiones propias) ----
def list_sessions(user: User) -> dict:
    if not is_available():
        return {"items": [], "available": False, "current_id": None}
    now = datetime.utcnow()
    rows = (UserSession.query.filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None),
                                     UserSession.expires_at > now)
            .order_by(UserSession.last_seen_at.desc()).all())
    current = current_record()
    current_id = current.id if current else None
    return {"items": [row.to_dict(current=row.id == current_id, inactive_after_hours=INACTIVE_AFTER_HOURS) for row in rows],
            "available": True, "current_id": current_id}


def _own_active(user: User):
    return UserSession.query.filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))


def revoke(user: User, session_id: int) -> dict:
    if not is_available():
        raise ValidationError("El registro de sesiones no está disponible.", status_code=503)
    record = _own_active(user).filter(UserSession.id == session_id).first()
    if record is None:
        raise NotFoundError("La sesión no existe o ya fue cerrada.")
    record.revoked_at = datetime.utcnow()
    db.session.commit()
    _notify(user, "Sesión cerrada", f"Se cerró la sesión de {record.device_label or 'un dispositivo'}.")
    current = current_record()
    return {"closed": 1, "current_closed": current is not None and current.id == record.id}


def revoke_others(user: User) -> dict:
    return _revoke_many(user, keep_current=True, only_inactive=False, label="las demás sesiones")


def revoke_all(user: User) -> dict:
    return _revoke_many(user, keep_current=False, only_inactive=False, label="todas las sesiones")


def revoke_inactive(user: User) -> dict:
    return _revoke_many(user, keep_current=True, only_inactive=True, label="las sesiones inactivas")


def _revoke_many(user: User, *, keep_current: bool, only_inactive: bool, label: str) -> dict:
    if not is_available():
        raise ValidationError("El registro de sesiones no está disponible.", status_code=503)
    now = datetime.utcnow()
    current = current_record()
    query = _own_active(user)
    if keep_current and current is not None:
        query = query.filter(UserSession.id != current.id)
    if only_inactive:
        query = query.filter(UserSession.last_seen_at < now - timedelta(hours=INACTIVE_AFTER_HOURS))
    rows = query.all()
    for row in rows:
        row.revoked_at = now
    db.session.commit()
    if rows:
        _notify(user, "Sesiones cerradas", f"Se cerraron {len(rows)} de {label}.")
    return {"closed": len(rows), "current_closed": not keep_current}


def _notify(user: User, title: str, message: str) -> None:
    from app.services import notification_service

    notification_service.security_event(title, message, actor=user, recipients=[user], dedupe_key=None)
