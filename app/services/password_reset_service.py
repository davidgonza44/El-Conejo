"""Recuperación de contraseña mediante enlace temporal enviado por correo.

Seguridad:
- Solo se guarda el hash SHA-256 del token; el token plano viaja únicamente
  dentro del enlace del correo (y de dev_reset_link en development).
- El endpoint de solicitud responde siempre lo mismo (respuesta neutra) para
  no revelar si un correo está registrado (evita enumeración de usuarios).
- El token expira (PASSWORD_RESET_TOKEN_MINUTES), es de un solo uso y los
  tokens anteriores no usados se invalidan al solicitar uno nuevo.
- Confirmar el cambio NO inicia sesión: el usuario debe ir a /login.
- Los logs nunca incluyen el token ni la contraseña.
"""
import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta

from flask import current_app, request

from app.extensions import db
from app.models import PasswordResetToken, User
from app.services import email_service, password_policy_service
from app.services.exceptions import ValidationError

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

NEUTRAL_MESSAGE = (
    "Si el correo está registrado, recibirá un enlace para restablecer su contraseña."
)

INVALID_TOKEN_MESSAGE = "El enlace de recuperación es inválido."


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _clean_email(data: dict) -> str:
    email = (data.get("email") or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise ValidationError("El campo 'email' no tiene un formato válido.")
    return email


def _is_development() -> bool:
    return current_app.config["APP_ENV"] == "development"


def _send_reset_email(user: User, reset_link: str, minutes: int) -> None:
    """Envía el enlace por correo real cuando MAIL_ENABLED=true.

    En development los errores de correo se propagan como JSON claro para
    poder diagnosticarlos; en production solo se registran y la respuesta
    sigue siendo neutra (anti-enumeración).
    """
    if not email_service.is_enabled():
        return
    try:
        email_service.send_template_email(
            to=user.email,
            subject=f"Restablecer contraseña — {current_app.config['MAIL_FROM_NAME']}",
            template="password_reset",
            user_name=user.name,
            reset_link=reset_link,
            expires_minutes=minutes,
        )
    except email_service.EmailError:
        if _is_development():
            raise
        logger.warning(
            "No se pudo enviar el correo de recuperación a %s.",
            email_service.mask_email(user.email),
        )


def request_reset(data: dict) -> dict:
    """Genera un token de recuperación si el email pertenece a un usuario activo.

    La respuesta es neutra en todos los casos y nunca inicia sesión. En
    APP_ENV=development se incluye dev_reset_link para pruebas.
    """
    email = _clean_email(data)
    response: dict = {"message": NEUTRAL_MESSAGE}

    user = User.query.filter_by(email=email).first()
    if user is None or not user.is_active:
        # Respuesta idéntica a la del caso exitoso: no revelar nada.
        return response

    # Invalida tokens anteriores no usados de este usuario (solo vale el último).
    PasswordResetToken.query.filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None),
    ).update({PasswordResetToken.used_at: datetime.utcnow()})

    token = secrets.token_urlsafe(32)
    minutes = current_app.config["PASSWORD_RESET_TOKEN_MINUTES"]
    db.session.add(
        PasswordResetToken(
            user_id=user.id,
            email=email,
            token_hash=_hash_token(token),
            expires_at=datetime.utcnow() + timedelta(minutes=minutes),
            request_ip=request.remote_addr,
            user_agent=(request.headers.get("User-Agent") or "")[:255] or None,
        )
    )
    db.session.commit()

    base_url = current_app.config["APP_BASE_URL"].rstrip("/")
    reset_link = f"{base_url}/reset-password?token={token}"

    logger.info(
        "Token de recuperación creado para %s.", email_service.mask_email(email)
    )

    _send_reset_email(user, reset_link, minutes)

    if _is_development():
        response["dev_reset_link"] = reset_link
        response["expires_in_minutes"] = minutes

    return response


def confirm_reset(data: dict) -> dict:
    """Valida el token y actualiza la contraseña. No inicia sesión."""
    token = (data.get("token") or "").strip()
    if not token:
        raise ValidationError("El campo 'token' es obligatorio.")

    new_password = password_policy_service.validate(data.get("new_password"))
    confirm_password = data.get("confirm_password") or ""
    if new_password != confirm_password:
        raise ValidationError("Las contraseñas no coinciden.")

    record = PasswordResetToken.query.filter_by(token_hash=_hash_token(token)).first()
    if record is None:
        raise ValidationError(INVALID_TOKEN_MESSAGE, status_code=401)
    if record.is_used:
        raise ValidationError(
            "El enlace de recuperación ya fue utilizado. Solicite uno nuevo.",
            status_code=401,
        )
    if record.is_expired:
        raise ValidationError(
            "El enlace de recuperación ha expirado. Solicite uno nuevo.",
            status_code=401,
        )

    user = db.session.get(User, record.user_id) if record.user_id else None
    if user is None or record.email.lower() != user.email.lower():
        raise ValidationError(INVALID_TOKEN_MESSAGE, status_code=401)
    if not user.is_active:
        raise ValidationError(
            "El usuario está inactivo. Contacte al administrador.", status_code=401
        )

    # Historial: la nueva contraseña no puede coincidir con las últimas N del usuario.
    password_policy_service.validate(new_password, user)
    password_policy_service.apply(user, new_password)
    # El usuario demostró control del correo al abrir el enlace.
    user.email_verified = True
    record.used_at = datetime.utcnow()
    db.session.commit()

    logger.info(
        "Contraseña restablecida para %s.", email_service.mask_email(user.email)
    )

    return {
        "message": (
            "Contraseña actualizada correctamente. "
            "Inicie sesión con su nueva contraseña."
        ),
        "login_url": "/login",
    }
