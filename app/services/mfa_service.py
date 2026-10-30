"""Autenticación en dos pasos (TOTP), códigos de recuperación y dispositivos confiables.

- El secreto TOTP se guarda cifrado (crypto_service) y solo se muestra durante el enrolamiento.
- 2FA queda habilitado únicamente tras verificar un código válido.
- Los códigos de recuperación se guardan como SHA-256 y se muestran una sola vez.
- El desafío de login vive en la sesión Flask (sin login_user) hasta verificar el código.
- Sustituir un factor ya activo exige re-autenticación fuerte (código vigente y contraseña) en esta sesión.
"""
import hashlib
import secrets
import time
from datetime import datetime, timedelta

import pyotp
import qrcode
from flask import current_app, request, session
from qrcode.image.svg import SvgPathImage

from app.extensions import db
from app.models import MfaRecoveryCode, TrustedDevice, User, UserMfa
from app.services import crypto_service, login_event_service
from app.services.exceptions import NotFoundError, ValidationError
from app.utils.schema import table_ready

CHALLENGE_KEY = "mfa_challenge"
CHALLENGE_MINUTES = 5
REPLACE_KEY = "mfa_replace_authorized"
REPLACE_MINUTES = 10
RECOVERY_CODES = 10
TRUSTED_COOKIE = "fc_trusted_device"


def is_available() -> bool:
    return table_ready(UserMfa.__tablename__) and table_ready(MfaRecoveryCode.__tablename__)


def devices_available() -> bool:
    return table_ready(TrustedDevice.__tablename__)


def _require() -> None:
    if not is_available():
        raise ValidationError("La verificación en dos pasos no está disponible: falta la migración.", status_code=503)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _row(user: User) -> UserMfa | None:
    return db.session.get(UserMfa, user.id) if is_available() else None


def is_enabled(user: User) -> bool:
    row = _row(user)
    return row is not None and row.enabled


def status(user: User) -> dict:
    row = _row(user)
    remaining = 0
    if row is not None and row.enabled:
        remaining = MfaRecoveryCode.query.filter_by(user_id=user.id, used_at=None).count()
    return {
        "available": is_available(),
        "enabled": row is not None and row.enabled,
        "pending": row is not None and bool(row.pending_secret_encrypted),
        "enabled_at": row.enabled_at.isoformat() if row is not None and row.enabled_at else None,
        "recovery_codes_remaining": remaining,
        "trusted_devices": list_trusted_devices(user)["items"] if devices_available() else [],
    }


# ---- enrolamiento ----
def start_enrollment(user: User, data: dict | None = None) -> dict:
    """Genera un secreto pendiente y devuelve QR (SVG) + clave manual. Nada queda habilitado aún.

    Con 2FA activo, el reemplazo exige antes el código vigente (o de recuperación) y la contraseña actual;
    el factor anterior sigue vigente hasta confirmar el nuevo.
    """
    _require()
    row = _row(user) or UserMfa(user_id=user.id)
    if row.enabled:
        _reauthenticate(user, data or {})
        session[REPLACE_KEY] = {"uid": user.id,
                                "exp": (datetime.utcnow() + timedelta(minutes=REPLACE_MINUTES)).timestamp()}
    secret = pyotp.random_base32()
    row.pending_secret_encrypted = crypto_service.encrypt(secret)
    db.session.add(row)
    db.session.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=current_app.config.get("MFA_ISSUER"))
    svg = qrcode.make(uri, image_factory=SvgPathImage, box_size=6, border=2).to_string(encoding="unicode")
    return {"qr_svg": svg, "setup_key": secret, "otpauth_uri": uri, "already_enabled": row.enabled}


def _clean_code(value) -> str:
    code = (value or "").strip().replace(" ", "").replace("-", "").upper() if isinstance(value, str) else ""
    if not code:
        raise ValidationError("Ingrese el código de verificación.")
    return code


def _verify_totp(row: UserMfa, secret: str, code: str) -> bool:
    totp = pyotp.TOTP(secret)
    if not code.isdigit():
        return False
    now_step = int(time.time()) // totp.interval
    # Se acepta ±1 intervalo (reloj desfasado) y se guarda el paso del código aceptado:
    # anti-replay exacto, el mismo código no vale dos veces pero el del siguiente intervalo sí.
    matched = next((s for s in (now_step - 1, now_step, now_step + 1) if pyotp.utils.strings_equal(totp.at(s * totp.interval), code)), None)
    if matched is None or (row.last_verified_step is not None and matched <= row.last_verified_step):
        return False
    row.last_verified_step = matched
    return True


def confirm_enrollment(user: User, data: dict) -> dict:
    """Verifica el código contra el secreto pendiente; solo entonces habilita 2FA y emite códigos de recuperación."""
    _require()
    row = _row(user)
    if row is None or not row.pending_secret_encrypted:
        raise ValidationError("No hay un enrolamiento pendiente. Genere primero el código QR.")
    replacing = row.enabled
    if replacing and not _replace_authorized(user):
        raise ValidationError("Confirme su identidad (código actual y contraseña) antes de reemplazar el factor 2FA.")
    code = _clean_code(data.get("code"))
    secret = crypto_service.decrypt(row.pending_secret_encrypted)
    previous_step = row.last_verified_step
    if replacing:
        # El anti-replay es por secreto: el paso consumido al re-autenticar con el factor anterior no aplica al nuevo.
        row.last_verified_step = None
    if not _verify_totp(row, secret, code):
        db.session.rollback()
        raise ValidationError("El código no es válido. Verifique la hora del dispositivo e intente de nuevo.")
    if previous_step is not None:
        row.last_verified_step = max(row.last_verified_step, previous_step)
    session.pop(REPLACE_KEY, None)
    row.secret_encrypted = row.pending_secret_encrypted
    row.pending_secret_encrypted = None
    row.enabled_at = datetime.utcnow()
    codes = _issue_recovery_codes(user)
    db.session.commit()
    if replacing:
        _notify(user, "Factor 2FA reemplazado", "Se reemplazó la aplicación de autenticación; los códigos anteriores dejaron de ser válidos.")
    else:
        _notify(user, "Verificación en dos pasos activada", "Se activó 2FA (TOTP) en su cuenta.")
    return {"enabled": True, "replaced": replacing, "recovery_codes": codes}


def _replace_authorized(user: User) -> bool:
    grant = session.get(REPLACE_KEY)
    return bool(grant and grant.get("uid") == user.id and grant.get("exp", 0) >= datetime.utcnow().timestamp())


def _issue_recovery_codes(user: User) -> list[str]:
    MfaRecoveryCode.query.filter_by(user_id=user.id).delete()
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = secrets.token_hex(4).upper()
        codes.append(f"{raw[:4]}-{raw[4:]}")
        db.session.add(MfaRecoveryCode(user_id=user.id, code_hash=_hash(raw)))
    return codes


def _consume_recovery(user: User, code: str) -> bool:
    record = MfaRecoveryCode.query.filter_by(user_id=user.id, code_hash=_hash(code), used_at=None).first()
    if record is None:
        return False
    record.used_at = datetime.utcnow()
    return True


def verify_code(user: User, value) -> str:
    """Acepta TOTP o código de recuperación (un solo uso). Devuelve 'totp' | 'recovery'; lanza si falla."""
    row = _row(user)
    if row is None or not row.enabled:
        raise ValidationError("La verificación en dos pasos no está activada.")
    code = _clean_code(value)
    if len(code) == 6 and code.isdigit() and _verify_totp(row, crypto_service.decrypt(row.secret_encrypted), code):
        return "totp"
    if len(code) == 8 and _consume_recovery(user, code):
        return "recovery"
    db.session.rollback()
    raise ValidationError("Código de verificación incorrecto.")


def disable(user: User, data: dict) -> None:
    """Re-autenticación fuerte: código TOTP/recuperación válido (y contraseña actual si el usuario tiene una)."""
    _require()
    _reauthenticate(user, data)
    row = _row(user)
    db.session.delete(row)
    MfaRecoveryCode.query.filter_by(user_id=user.id).delete()
    if devices_available():
        TrustedDevice.query.filter_by(user_id=user.id, revoked_at=None).update({TrustedDevice.revoked_at: datetime.utcnow()})
    db.session.commit()
    _notify(user, "Verificación en dos pasos desactivada", "Se desactivó 2FA; los dispositivos confiables fueron revocados.")


def regenerate_recovery_codes(user: User, data: dict) -> dict:
    _require()
    _reauthenticate(user, data)
    codes = _issue_recovery_codes(user)
    db.session.commit()
    _notify(user, "Códigos de recuperación regenerados", "Los códigos anteriores dejaron de ser válidos.")
    return {"recovery_codes": codes}


def _reauthenticate(user: User, data: dict) -> None:
    verify_code(user, data.get("code"))
    if user.password_hash and not user.check_password(data.get("current_password") or ""):
        db.session.rollback()
        raise ValidationError("La contraseña actual no es correcta.")


# ---- desafío durante el login ----
def requires_challenge(user: User) -> bool:
    return is_enabled(user) and not _trusted_cookie_valid(user)


def begin_challenge(user: User, provider: str) -> None:
    session.clear()
    session[CHALLENGE_KEY] = {"uid": user.id, "provider": provider,
                              "exp": (datetime.utcnow() + timedelta(minutes=CHALLENGE_MINUTES)).timestamp()}


def pending_challenge() -> dict | None:
    data = session.get(CHALLENGE_KEY)
    if not data or data.get("exp", 0) < datetime.utcnow().timestamp():
        session.pop(CHALLENGE_KEY, None)
        return None
    return data


def complete_challenge(data: dict) -> tuple[User, str | None]:
    """Verifica el código del desafío y abre la sesión. Devuelve (usuario, token de dispositivo confiable|None)."""
    from app.services import session_service

    pending = pending_challenge()
    if pending is None:
        raise ValidationError("No hay una verificación pendiente o expiró. Inicie sesión nuevamente.", status_code=401)
    user = db.session.get(User, pending["uid"])
    if user is None or not user.is_active:
        session.pop(CHALLENGE_KEY, None)
        raise ValidationError("Credenciales inválidas.", status_code=401)
    try:
        verify_code(user, data.get("code"))
    except ValidationError:
        login_event_service.record(user.email, False, reason="mfa_failed", user=user, provider=pending["provider"])
        raise
    session.pop(CHALLENGE_KEY, None)
    db.session.commit()
    session_service.open_session(user, provider=pending["provider"])
    login_event_service.record(user.email, True, reason="mfa_ok", user=user, provider=pending["provider"])
    token = trust_current_device(user) if data.get("trust_device") is True else None
    return user, token


# ---- dispositivos confiables ----
def _trusted_cookie_valid(user: User) -> bool:
    raw = request.cookies.get(TRUSTED_COOKIE) if request else None
    if not raw or not devices_available():
        return False
    device = TrustedDevice.query.filter_by(user_id=user.id, token_hash=_hash(raw)).first()
    if device is None or not device.is_valid:
        return False
    device.last_used_at = datetime.utcnow()
    db.session.commit()
    return True


def trust_current_device(user: User, label: str | None = None) -> str:
    """Crea un dispositivo confiable para el navegador actual y devuelve el token para la cookie."""
    if not devices_available():
        raise ValidationError("Los dispositivos confiables no están disponibles: falta la migración.", status_code=503)
    ip, agent = login_event_service.request_meta()
    token = secrets.token_urlsafe(32)
    db.session.add(TrustedDevice(user_id=user.id, token_hash=_hash(token), label=label or login_event_service.device_label(agent),
                                 user_agent=agent, ip_address=ip,
                                 expires_at=datetime.utcnow() + timedelta(days=current_app.config.get("TRUSTED_DEVICE_DAYS", 30))))
    db.session.commit()
    return token


def add_trusted_device(user: User, data: dict) -> str:
    """Desde Configuración: exige un código 2FA válido antes de confiar en este navegador."""
    label = (data.get("label") or "").strip() if isinstance(data.get("label"), str) else ""
    if len(label) > 80:
        raise ValidationError("El nombre del dispositivo no puede superar 80 caracteres.")
    verify_code(user, data.get("code"))
    db.session.commit()
    token = trust_current_device(user, label or None)
    _notify(user, "Dispositivo confiable agregado", "Este navegador omitirá el código 2FA durante su vigencia.")
    return token


def cookie_kwargs() -> dict:
    return {"max_age": current_app.config.get("TRUSTED_DEVICE_DAYS", 30) * 86400, "httponly": True,
            "samesite": "Lax", "secure": request.is_secure}


def list_trusted_devices(user: User) -> dict:
    if not devices_available():
        return {"items": [], "available": False}
    raw = request.cookies.get(TRUSTED_COOKIE) if request else None
    current_hash = _hash(raw) if raw else None
    now = datetime.utcnow()
    rows = (TrustedDevice.query.filter(TrustedDevice.user_id == user.id, TrustedDevice.revoked_at.is_(None),
                                       TrustedDevice.expires_at > now).order_by(TrustedDevice.last_used_at.desc()).all())
    return {"items": [row.to_dict(current=row.token_hash == current_hash) for row in rows], "available": True}


def revoke_trusted_device(user: User, device_id: int) -> None:
    device = TrustedDevice.query.filter_by(user_id=user.id, id=device_id, revoked_at=None).first()
    if device is None:
        raise NotFoundError("El dispositivo no existe o ya fue revocado.")
    device.revoked_at = datetime.utcnow()
    db.session.commit()
    _notify(user, "Dispositivo confiable revocado", f"Se revocó {device.label}.")


def revoke_all_trusted_devices(user: User) -> int:
    count = TrustedDevice.query.filter_by(user_id=user.id, revoked_at=None).update({TrustedDevice.revoked_at: datetime.utcnow()})
    db.session.commit()
    if count:
        _notify(user, "Dispositivos confiables revocados", f"Se revocaron {count} dispositivo(s).")
    return count


def _notify(user: User, title: str, message: str) -> None:
    from app.services import notification_service

    notification_service.security_event(title, message, actor=user, recipients=[user], dedupe_key=None)
