"""Adaptadores de canales externos: correo (SMTP existente), WhatsApp (Meta Cloud API) y SMS (Twilio).

- Sin credenciales en código: todo viene de variables de entorno (.env). Ninguna API devuelve secretos.
- Con NOTIFICATIONS_DRY_RUN=true (pruebas) no se hace red: los envíos se registran en memoria.
- ``missing(channel)`` devuelve solo NOMBRES de variables faltantes para la ayuda de configuración.
"""
import requests
from flask import current_app

from app.models.notification import CHANNEL_APP, CHANNEL_EMAIL, CHANNEL_SMS, CHANNEL_WHATSAPP
from app.services import email_service
from app.services.exceptions import ApiError

LABELS = {CHANNEL_EMAIL: "Correo electrónico", CHANNEL_WHATSAPP: "WhatsApp", CHANNEL_SMS: "SMS"}
_REQUIRED = {
    CHANNEL_EMAIL: ("MAIL_ENABLED", "MAIL_SERVER", "MAIL_PORT", "MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_DEFAULT_SENDER"),
    CHANNEL_WHATSAPP: ("WHATSAPP_ENABLED", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ACCESS_TOKEN"),
    CHANNEL_SMS: ("SMS_ENABLED", "SMS_PROVIDER", "SMS_ACCOUNT_SID", "SMS_AUTH_TOKEN", "SMS_FROM_NUMBER"),
}
_TIMEOUT = 15
_SUPPORTED_SMS_PROVIDERS = ("twilio",)

dry_run_log: list[dict] = []  # solo con NOTIFICATIONS_DRY_RUN=true


class ProviderError(ApiError):
    status_code = 502


def missing(channel: str) -> list[str]:
    config = current_app.config
    names = [name for name in _REQUIRED[channel] if not config.get(name)]
    if channel == CHANNEL_SMS and config.get("SMS_PROVIDER") and config["SMS_PROVIDER"] not in _SUPPORTED_SMS_PROVIDERS:
        names.append("SMS_PROVIDER (solo 'twilio' soportado)")
    return names


def configured(channel: str) -> bool:
    return not missing(channel)


def destination(channel: str, settings: dict) -> str:
    from app.services import settings_service as s

    key = {CHANNEL_EMAIL: s.EMAIL_TO, CHANNEL_WHATSAPP: s.WHATSAPP_TO, CHANNEL_SMS: s.SMS_TO}[channel]
    return settings.get(key) or ""


def status_all(settings: dict) -> list[dict]:
    """Estado de los cuatro canales; la app (bandeja/campana) es interna y siempre está disponible."""
    from app.services import settings_service as s

    app_channel = {"channel": CHANNEL_APP, "label": "Notificaciones en la app", "configured": True, "missing": [],
                   "enabled": bool(settings.get(s.CHANNEL_ENABLED_KEYS[CHANNEL_APP])), "destination": ""}
    return [app_channel] + [{
        "channel": channel, "label": LABELS[channel], "configured": configured(channel), "missing": missing(channel),
        "enabled": bool(settings.get(s.CHANNEL_ENABLED_KEYS[channel])), "destination": destination(channel, settings),
    } for channel in LABELS]


def send(channel: str, to: str, *, subject: str, text: str, html: str | None = None) -> str:
    """Envía por el canal indicado y devuelve un detalle seguro. Lanza ProviderError si falla."""
    if missing(channel):
        raise ProviderError(f"{LABELS[channel]}: proveedor no configurado.")
    if not to:
        raise ProviderError(f"{LABELS[channel]}: destinatario no configurado.")
    if current_app.config.get("NOTIFICATIONS_DRY_RUN"):
        dry_run_log.append({"channel": channel, "to": to, "subject": subject, "text": text})
        return f"Simulado (dry run) hacia {_mask(to)}"
    if channel == CHANNEL_EMAIL:
        return _send_email(to, subject, text, html)
    if channel == CHANNEL_WHATSAPP:
        return _send_whatsapp(to, text)
    return _send_sms(to, text)


def _mask(value: str) -> str:
    if "@" in value:
        return email_service.mask_email(value)
    return f"{value[:4]}***{value[-2:]}" if len(value) > 6 else "***"


def _send_email(to: str, subject: str, text: str, html: str | None) -> str:
    try:
        email_service.send_email(to, subject, html or f"<pre>{text}</pre>", text_body=text)
    except email_service.EmailError as exc:
        raise ProviderError(f"Correo: {exc.message}") from exc
    return f"Correo enviado a {_mask(to)}"


def _send_whatsapp(to: str, text: str) -> str:
    config = current_app.config
    url = f"https://graph.facebook.com/{config['WHATSAPP_API_VERSION']}/{config['WHATSAPP_PHONE_NUMBER_ID']}/messages"
    payload = {"messaging_product": "whatsapp", "to": to.lstrip("+"), "type": "text", "text": {"body": text[:4000]}}
    try:
        response = requests.post(url, json=payload, timeout=_TIMEOUT,
                                 headers={"Authorization": f"Bearer {config['WHATSAPP_ACCESS_TOKEN']}"})
    except requests.RequestException as exc:
        raise ProviderError(f"WhatsApp: error de red ({type(exc).__name__}).") from exc
    if response.status_code >= 400:
        raise ProviderError(f"WhatsApp: la API respondió {response.status_code}.")
    return f"WhatsApp enviado a {_mask(to)}"


def _send_sms(to: str, text: str) -> str:
    config = current_app.config
    url = f"https://api.twilio.com/2010-04-01/Accounts/{config['SMS_ACCOUNT_SID']}/Messages.json"
    try:
        response = requests.post(url, data={"To": to, "From": config["SMS_FROM_NUMBER"], "Body": text[:1500]},
                                 auth=(config["SMS_ACCOUNT_SID"], config["SMS_AUTH_TOKEN"]), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise ProviderError(f"SMS: error de red ({type(exc).__name__}).") from exc
    if response.status_code >= 400:
        raise ProviderError(f"SMS: Twilio respondió {response.status_code}.")
    return f"SMS enviado a {_mask(to)}"
