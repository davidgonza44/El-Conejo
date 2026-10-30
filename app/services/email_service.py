"""Servicio de correo saliente (SMTP), reutilizable por todo el sistema.

Usos actuales: recuperación de contraseña y login passwordless.
Usos futuros: avisos del sistema (stock bajo, reportes, etc.).

Seguridad:
- La configuración viene de variables de entorno (.env); no hay credenciales
  en el código.
- La conexión usa STARTTLS o SSL con ssl.create_default_context(), que
  mantiene activa la verificación de certificados.
- Los logs enmascaran el destinatario y nunca incluyen MAIL_PASSWORD ni el
  cuerpo del mensaje (que puede contener tokens).
"""
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app, render_template

from app.services.exceptions import ApiError

logger = logging.getLogger(__name__)

_REQUIRED_CONFIG_KEYS = (
    "MAIL_SERVER",
    "MAIL_PORT",
    "MAIL_USERNAME",
    "MAIL_PASSWORD",
    "MAIL_DEFAULT_SENDER",
)

_SMTP_TIMEOUT_SECONDS = 20


class EmailError(ApiError):
    """Fallo controlado del envío de correo (configuración o SMTP)."""

    status_code = 500


def is_enabled() -> bool:
    """True si el envío de correo real está habilitado (MAIL_ENABLED=true)."""
    return bool(current_app.config.get("MAIL_ENABLED"))


def mask_email(email: str) -> str:
    """Enmascara un correo para logs: 'admin@x.com' -> 'a***@x.com'."""
    local, _, domain = (email or "").partition("@")
    if not local or not domain:
        return "***"
    return f"{local[0]}***@{domain}"


def _validate_config() -> None:
    missing = [key for key in _REQUIRED_CONFIG_KEYS if not current_app.config.get(key)]
    if missing:
        raise EmailError(
            "Configuración SMTP incompleta: defina "
            + ", ".join(missing)
            + " en el archivo .env."
        )


def send_email(to: str, subject: str, html_body: str, text_body: str | None = None) -> None:
    """Envía un correo por SMTP. Levanta EmailError ante cualquier fallo.

    El remitente se muestra con nombre: 'Ferretería El Conejo <correo>'.
    """
    config = current_app.config
    if not config.get("MAIL_ENABLED"):
        raise EmailError("El envío de correo está deshabilitado (MAIL_ENABLED=false).")
    _validate_config()

    message = EmailMessage()
    message["From"] = formataddr(
        (config.get("MAIL_FROM_NAME") or "", config["MAIL_DEFAULT_SENDER"])
    )
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text_body or "Este correo requiere un cliente compatible con HTML.")
    message.add_alternative(html_body, subtype="html")

    server = config["MAIL_SERVER"]
    port = int(config["MAIL_PORT"])
    # Contexto por defecto: verificación de certificados SSL activa.
    context = ssl.create_default_context()

    try:
        if config.get("MAIL_USE_SSL"):
            with smtplib.SMTP_SSL(
                server, port, timeout=_SMTP_TIMEOUT_SECONDS, context=context
            ) as smtp:
                smtp.login(config["MAIL_USERNAME"], config["MAIL_PASSWORD"])
                smtp.send_message(message)
        else:
            with smtplib.SMTP(server, port, timeout=_SMTP_TIMEOUT_SECONDS) as smtp:
                if config.get("MAIL_USE_TLS"):
                    smtp.starttls(context=context)
                smtp.login(config["MAIL_USERNAME"], config["MAIL_PASSWORD"])
                smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        logger.warning("Autenticación SMTP rechazada (código %s).", exc.smtp_code)
        raise EmailError(
            "El servidor SMTP rechazó las credenciales. Verifique MAIL_USERNAME y "
            "MAIL_PASSWORD (para Gmail use una contraseña de aplicación)."
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        logger.warning(
            "Fallo SMTP enviando a %s: %s", mask_email(to), type(exc).__name__
        )
        raise EmailError(
            "No fue posible enviar el correo. Verifique MAIL_SERVER, MAIL_PORT "
            "y la conexión de red."
        ) from exc

    logger.info("Correo enviado a %s (asunto: %s).", mask_email(to), subject)


def send_template_email(to: str, subject: str, template: str, **context) -> None:
    """Renderiza app/templates/emails/<template>.html y .txt y envía el correo."""
    context.setdefault(
        "app_name", current_app.config.get("MAIL_FROM_NAME") or "Ferretería El Conejo"
    )
    html_body = render_template(f"emails/{template}.html", **context)
    text_body = render_template(f"emails/{template}.txt", **context)
    send_email(to, subject, html_body, text_body=text_body)
