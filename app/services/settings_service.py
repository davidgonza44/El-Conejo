"""Configuración persistida: solo claves soportadas y consumidas por un módulo en tiempo de ejecución.

Cada clave tiene tipo, validador y valor por defecto; el navegador jamás puede crear claves
arbitrarias. Los valores por defecto viven aquí (la tabla puede estar vacía).
"""
import re
from datetime import datetime, timezone

from app.extensions import db
from app.models.app_setting import AppSetting
from app.models.notification import CHANNEL_APP, CHANNELS, EVENT_LOW_STOCK, EVENTS, EXTERNAL_CHANNELS
from app.services.exceptions import ApiError, ValidationError
from app.services import company_logo_service as logos
from sqlalchemy.exc import SQLAlchemyError

COMPANY_NAME = "company.name"                      # cabecera del PDF de nota de entrega
COMPANY_LOGO = "company.logo"
COMPANY_TAX_ID = "company.tax_id"                  # RIF impreso en el membrete de los documentos
LOW_STOCK_ALERTS = "alerts.low_stock_enabled"      # campana del shell y evento "Stock bajo / agotado" (canal app)
APP_NOTIFICATIONS = "notifications.app_enabled"    # canal "Notificaciones en la app" (campana)
EMAIL_NOTIFICATIONS = "notifications.email_enabled"
WHATSAPP_NOTIFICATIONS = "notifications.whatsapp_enabled"
SMS_NOTIFICATIONS = "notifications.sms_enabled"
EMAIL_TO = "notifications.email_to"
DIGEST_FREQUENCY = "notifications.digest_frequency"   # immediate | daily | weekly
DIGEST_TIME = "notifications.digest_time"             # HH:MM (hora local del servidor)
EMAIL_FORMAT = "notifications.email_format"           # html | text
WHATSAPP_TO = "notifications.whatsapp_to"
SMS_TO = "notifications.sms_to"
PASSWORD_MIN_LENGTH = "security.password_min_length"  # alta de usuario, cambio y recuperación de contraseña
PASSWORD_REQUIRE_UPPERCASE = "security.password_require_uppercase"
PASSWORD_REQUIRE_LOWERCASE = "security.password_require_lowercase"
PASSWORD_REQUIRE_NUMBER = "security.password_require_number"
PASSWORD_REQUIRE_SPECIAL = "security.password_require_special"
PASSWORD_EXPIRATION_DAYS = "security.password_expiration_days"  # 0 = nunca expira
PASSWORD_HISTORY_COUNT = "security.password_history_count"      # 0 = sin historial
BACKUP_FREQUENCY = "backup.auto_frequency"   # off | daily | weekly | monthly
BACKUP_TIME = "backup.auto_time"             # HH:MM
BACKUP_KIND = "backup.auto_kind"             # full | database
BACKUP_RETENTION_DAYS = "backup.retention_days"  # 0 = conservar todo
BACKUP_LOCATION = "backup.location"          # local | local_drive
BACKUP_DIRECTORY = "backup.directory"        # "" = BACKUP_DIR de la configuración

CHANNEL_ENABLED_KEYS = {CHANNEL_APP: APP_NOTIFICATIONS, "email": EMAIL_NOTIFICATIONS,
                        "whatsapp": WHATSAPP_NOTIFICATIONS, "sms": SMS_NOTIFICATIONS}

_COMPANY_NAME_MAX = 120
_PASSWORD_MIN, _PASSWORD_MAX = 6, 64
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?[1-9]\d{7,14}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _text(value, *, max_length: int):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("El nombre de la empresa es obligatorio.")
    if len(value.strip()) > max_length:
        raise ValidationError(f"El nombre de la empresa no puede exceder {max_length} caracteres.")
    return value.strip()


_RIF_RE = re.compile(r"[JGVEPC]-?[0-9]{8}-?[0-9]")


def _optional_rif(value):
    """RIF venezolano opcional (J-12345678-9). Vacío: los documentos no muestran RIF."""
    if value in (None, ""):
        return ""
    cleaned = value.strip().upper() if isinstance(value, str) else ""
    if not _RIF_RE.fullmatch(cleaned):
        raise ValidationError("El RIF debe tener el formato J-12345678-9.")
    digits = re.sub(r"[^0-9]", "", cleaned)
    return f"{cleaned[0]}-{digits[:8]}-{digits[8]}"


def _bool(value):
    if not isinstance(value, bool):
        raise ValidationError("El valor debe ser verdadero o falso.")
    return value


def _int_range(minimum: int, maximum: int, label: str):
    def validate(value):
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValidationError(f"{label} debe ser un entero entre {minimum} y {maximum}.")
        return value
    return validate


def _choice(options: tuple[str, ...], label: str):
    def validate(value):
        if value not in options:
            raise ValidationError(f"{label} debe ser uno de: {', '.join(options)}.")
        return value
    return validate


def _optional_email(value):
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not _EMAIL_RE.match(value.strip().lower()):
        raise ValidationError("El correo de notificaciones no tiene un formato válido.")
    return value.strip().lower()


def _optional_phone(value):
    if value in (None, ""):
        return ""
    cleaned = re.sub(r"[\s()-]", "", value) if isinstance(value, str) else ""
    if not _PHONE_RE.match(cleaned):
        raise ValidationError("El número debe estar en formato internacional, p. ej. +584121234567.")
    return cleaned


def _time(value):
    if not isinstance(value, str) or not _TIME_RE.match(value):
        raise ValidationError("La hora debe tener el formato HH:MM (24 horas).")
    return value


def _directory(value):
    if value in (None, ""):
        return ""
    from app.services import backup_service

    return backup_service.validate_directory(value)


def matrix_key(event: str, channel: str) -> str:
    """Clave persistida de la celda evento × canal; 'Stock bajo' en la app reutiliza alerts.low_stock_enabled."""
    if event == EVENT_LOW_STOCK and channel == CHANNEL_APP:
        return LOW_STOCK_ALERTS
    return f"notify.{event}_{channel}"


# clave -> (valor por defecto, validador/normalizador)
SUPPORTED = {
    COMPANY_LOGO: ("", logos.validate_setting),
    COMPANY_NAME: ("Ferretería y Construcciones El Conejo C.A.", lambda v: _text(v, max_length=_COMPANY_NAME_MAX)),
    COMPANY_TAX_ID: ("", _optional_rif),
    LOW_STOCK_ALERTS: (True, _bool),
    APP_NOTIFICATIONS: (True, _bool),
    EMAIL_NOTIFICATIONS: (False, _bool),
    WHATSAPP_NOTIFICATIONS: (False, _bool),
    SMS_NOTIFICATIONS: (False, _bool),
    EMAIL_TO: ("", _optional_email),
    DIGEST_FREQUENCY: ("immediate", _choice(("immediate", "daily", "weekly"), "La frecuencia")),
    DIGEST_TIME: ("08:00", _time),
    EMAIL_FORMAT: ("html", _choice(("html", "text"), "El formato")),
    WHATSAPP_TO: ("", _optional_phone),
    SMS_TO: ("", _optional_phone),
    PASSWORD_MIN_LENGTH: (6, _int_range(_PASSWORD_MIN, _PASSWORD_MAX, "La longitud mínima")),
    PASSWORD_REQUIRE_UPPERCASE: (False, _bool),
    PASSWORD_REQUIRE_LOWERCASE: (False, _bool),
    PASSWORD_REQUIRE_NUMBER: (False, _bool),
    PASSWORD_REQUIRE_SPECIAL: (False, _bool),
    PASSWORD_EXPIRATION_DAYS: (0, _int_range(0, 365, "La expiración (días)")),
    PASSWORD_HISTORY_COUNT: (0, _int_range(0, 24, "El historial de contraseñas")),
    BACKUP_FREQUENCY: ("off", _choice(("off", "daily", "weekly", "monthly"), "La frecuencia de respaldo")),
    BACKUP_TIME: ("02:00", _time),
    BACKUP_KIND: ("database", _choice(("full", "database"), "El tipo de respaldo automático")),
    BACKUP_RETENTION_DAYS: (30, _int_range(0, 3650, "La retención (días)")),
    BACKUP_LOCATION: ("local", _choice(("local", "local_drive"), "La ubicación")),
    BACKUP_DIRECTORY: ("", _directory),
}
for _event in EVENTS:
    for _channel in CHANNELS:
        SUPPORTED.setdefault(matrix_key(_event, _channel), (_channel == CHANNEL_APP, _bool))

# Claves que "Restablecer valores" devuelve a su valor por defecto, por pestaña.
TAB_KEYS = {
    "general": (COMPANY_NAME, COMPANY_TAX_ID, LOW_STOCK_ALERTS),
    "security": (PASSWORD_MIN_LENGTH, PASSWORD_REQUIRE_UPPERCASE, PASSWORD_REQUIRE_LOWERCASE, PASSWORD_REQUIRE_NUMBER,
                 PASSWORD_REQUIRE_SPECIAL, PASSWORD_EXPIRATION_DAYS, PASSWORD_HISTORY_COUNT),
    "notifications": tuple(k for k in SUPPORTED if k.startswith(("notifications.", "notify.")) or k == LOW_STOCK_ALERTS),
    "backup": (BACKUP_FREQUENCY, BACKUP_TIME, BACKUP_KIND, BACKUP_RETENTION_DAYS, BACKUP_LOCATION, BACKUP_DIRECTORY),
}


_table_ready = False


def is_available() -> bool:
    """True cuando existe app_settings (scripts/migrate_app_settings.py). Sin tabla rigen los defaults."""
    global _table_ready
    if not _table_ready:  # ponytail: se consulta information_schema hasta la primera vez que exista; nunca se re-verifica
        _table_ready = db.inspect(db.engine).has_table(AppSetting.__tablename__)
    return _table_ready


def get(key: str):
    default, validate = SUPPORTED[key]
    row = db.session.get(AppSetting, key) if is_available() else None
    if row is None:
        return default
    try:
        return validate(row.value_json)
    except ValidationError:
        return default  # valor corrupto en BD: el código sigue siendo la autoridad del tipo


def get_all() -> dict:
    return {key: get(key) for key in SUPPORTED}


def _cross_validate(merged: dict) -> None:
    """Reglas entre claves: canales externos solo con proveedor configurado; celdas externas solo con canal activo;
    ubicación 'local_drive' solo con Google Drive conectado."""
    from app.services import google_drive_service, notification_providers

    if merged[BACKUP_LOCATION] == "local_drive" and not google_drive_service.status()["connected"]:
        raise ValidationError("Conecte Google Drive antes de elegir la ubicación 'Local + Google Drive'.")
    for channel in EXTERNAL_CHANNELS:
        enabled_key = CHANNEL_ENABLED_KEYS[channel]
        if merged[enabled_key] and not notification_providers.configured(channel):
            raise ValidationError(f"El canal {notification_providers.LABELS[channel]} no puede activarse: proveedor no configurado.")
        if merged[enabled_key] and not notification_providers.destination(channel, merged):
            raise ValidationError(f"Indique el destinatario del canal {notification_providers.LABELS[channel]} antes de activarlo.")
        for event in EVENTS:
            if merged[matrix_key(event, channel)] and not merged[enabled_key]:
                raise ValidationError(f"Active el canal {notification_providers.LABELS[channel]} antes de asignarle eventos.")


def update(values, user, *, logo=None, delete_logo=False) -> dict:
    """Valida y persiste solo claves soportadas; una clave inválida rechaza todo el lote."""
    if not isinstance(values, dict) or not values and not delete_logo:
        raise ValidationError("Envíe un objeto con al menos una configuración.")
    if COMPANY_LOGO in values:
        raise ValidationError("El logo solo puede cambiarse mediante la carga de una imagen.")
    if logo is not None and delete_logo:
        raise ValidationError("No puede cargar y eliminar el logo al mismo tiempo.")
    if not is_available():
        raise ApiError("La tabla app_settings no existe. Ejecute scripts/migrate_app_settings.py.", status_code=503)
    unknown = sorted(set(values) - set(SUPPORTED))
    if unknown:
        raise ValidationError(f"Configuración no soportada: {', '.join(unknown)}.")
    normalized = {key: SUPPORTED[key][1](value) for key, value in values.items()}
    _cross_validate({**get_all(), **normalized})
    if BACKUP_DIRECTORY in normalized:
        from app.services import backup_service

        backup_service.guard_directory_change(normalized[BACKUP_DIRECTORY])
    image = logos.normalize(logo) if logo is not None else None
    old_logo = get(COMPANY_LOGO)
    new_logo = None
    try:
        if image is not None:
            new_logo = logos.store(image)
            normalized[COMPANY_LOGO] = new_logo
        if delete_logo:
            row = db.session.get(AppSetting, COMPANY_LOGO)
            if row is not None:
                db.session.delete(row)
        _persist(normalized, user)
        db.session.commit()
    except (OSError, SQLAlchemyError) as exc:
        db.session.rollback()
        if new_logo:
            logos.remove(new_logo)
        raise ApiError("No se pudo guardar el logo." if image is not None or delete_logo else
                       "No se pudo guardar la configuración.", status_code=500) from exc
    if (new_logo or delete_logo) and old_logo:
        logos.remove(old_logo)
    return get_all()


def _persist(normalized, user):
    now = datetime.now(timezone.utc)
    for key, value in normalized.items():
        row = db.session.get(AppSetting, key) or AppSetting(setting_key=key)
        row.value_json, row.updated_at, row.updated_by_user_id = value, now, user.id
        db.session.add(row)


def reset_tab(tab: str) -> dict:
    """Devuelve a su valor por defecto solo las claves de la pestaña (borra sus filas). Nunca toca otras tablas."""
    if tab not in TAB_KEYS:
        raise ValidationError("Esta pestaña no tiene valores restablecibles.")
    if not is_available():
        raise ApiError("La tabla app_settings no existe. Ejecute scripts/migrate_app_settings.py.", status_code=503)
    if tab == "backup":  # volver a la carpeta por defecto tampoco puede dejar respaldos inaccesibles
        from app.services import backup_service

        backup_service.guard_directory_change("")
    AppSetting.query.filter(AppSetting.setting_key.in_(TAB_KEYS[tab])).delete(synchronize_session=False)
    db.session.commit()
    return get_all()
