"""Configuración de la aplicación leída desde variables de entorno (.env)."""
import os
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: str = "false") -> bool:
    """Interpreta variables de entorno booleanas ('true', '1', 'yes')."""
    return os.getenv(name, default).strip().lower() in ("true", "1", "yes")


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "clave-insegura-solo-desarrollo")

    # 'development' o 'production'. En desarrollo, el token passwordless se
    # devuelve en la respuesta para pruebas; en producción, jamás.
    APP_ENV = os.getenv("APP_ENV", "development")

    # URL base pública usada para armar enlaces enviados por correo
    # (p. ej. el enlace de restablecimiento de contraseña).
    APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:5000")

    # Minutos de vigencia del token passwordless.
    PASSWORDLESS_TOKEN_MINUTES = int(os.getenv("PASSWORDLESS_TOKEN_MINUTES", "15"))

    # Minutos de vigencia del token de recuperación de contraseña.
    PASSWORD_RESET_TOKEN_MINUTES = int(os.getenv("PASSWORD_RESET_TOKEN_MINUTES", "30"))

    # --- Correo saliente (SMTP) ---
    # Con MAIL_ENABLED=false no se envía correo real; en development los
    # flujos devuelven dev_token / dev_reset_link para poder probarlos.
    # MAIL_PASSWORD nunca debe imprimirse en consola ni en logs.
    MAIL_ENABLED = _env_bool("MAIL_ENABLED", "false")
    MAIL_SERVER = os.getenv("MAIL_SERVER", "")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USE_TLS = _env_bool("MAIL_USE_TLS", "true")
    MAIL_USE_SSL = _env_bool("MAIL_USE_SSL", "false")
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "")
    MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "Ferretería El Conejo")

    # Google OIDC (login con Google). Sin valores por defecto: se configuran
    # en .env con las credenciales creadas en Google Cloud Console.
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
    GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")

    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "3306")
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_NAME = os.getenv("DB_NAME", "ferreteria_conejo")

    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{DB_USER}:{quote_plus(DB_PASSWORD)}"
        f"@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    if os.getenv("CONEJO_CONTAINER") == "1":
        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_pre_ping": True, "pool_recycle": 1800, "hide_parameters": True,
            "connect_args": {"connect_timeout": 5},
        }
    BACKUP_SCHEDULER = os.getenv("BACKUP_SCHEDULER", "windows")
    # Explicit, timezone-aware rollout instant. Empty keeps daily closes disabled.
    INVENTORY_SNAPSHOT_ACTIVATED_AT = os.getenv("INVENTORY_SNAPSHOT_ACTIVATED_AT", "")

    # Versión mostrada en Configuración > Información del sistema y en GET /.
    APP_VERSION = "0.1.0"

    # --- Respaldo manual de la base de datos (solo admin) ---
    # Ruta al ejecutable mysqldump; vacío = respaldo no disponible (estado honesto en la UI).
    MYSQLDUMP_BIN = os.getenv("MYSQLDUMP_BIN", "")
    # Carpeta de destino; relativa a instance/ salvo que sea absoluta. Excluida de Git.
    BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
    # Transport-owned landing is read-only to the app; inbox/archive are private.
    # Relative paths resolve below Flask's instance directory.
    RECONCILIATION_LANDING_PATH = os.getenv("RECONCILIATION_LANDING_PATH", "reconciliation_landing")
    RECONCILIATION_SETTLE_SECONDS = 2
    RECONCILIATION_INBOX_PATH = os.getenv("RECONCILIATION_INBOX_PATH", "reconciliation_inbox")
    RECONCILIATION_ARCHIVE_PATH = os.getenv("RECONCILIATION_ARCHIVE_PATH", "reconciliation_archive")
    # Caracas calendar date of explicit rollout. Empty disables retrospective cut discovery.
    RECONCILIATION_ACTIVATED_ON = os.getenv("RECONCILIATION_ACTIVATED_ON", "")
    RECONCILIATION_ACTIVATED_AT = os.getenv("RECONCILIATION_ACTIVATED_AT", "")
    RECONCILIATION_AUTO_INGEST_ENABLED = _env_bool("RECONCILIATION_AUTO_INGEST_ENABLED", "true")
    # Fuentes de cortes automáticos acreditadas para proponer ajustes de stock (separadas por coma).
    # Vacío = ninguna: la conciliación solo informa diferencias. Un CSV subido a mano nunca acredita
    # un ajuste; la doble aprobación no sustituye la acreditación de la fuente.
    RECONCILIATION_ACCREDITED_SOURCES = tuple(
        source.strip() for source in os.getenv("RECONCILIATION_ACCREDITED_SOURCES", "").split(",") if source.strip())
    # Cliente mysql/mariadb usado SOLO para restaurar respaldos; vacío = restauración de BD no disponible.
    MYSQL_BIN = os.getenv("MYSQL_BIN", "")

    # --- Seguridad: cifrado de secretos en reposo (TOTP, tokens OAuth de Drive) ---
    # Clave Fernet urlsafe-base64 de 32 bytes; vacío = se deriva de SECRET_KEY (solo desarrollo).
    SECRETS_ENCRYPTION_KEY = os.getenv("SECRETS_ENCRYPTION_KEY", "")
    MFA_ISSUER = os.getenv("MFA_ISSUER", "Ferretería El Conejo")
    SESSION_MAX_DAYS = int(os.getenv("SESSION_MAX_DAYS", "30"))
    TRUSTED_DEVICE_DAYS = int(os.getenv("TRUSTED_DEVICE_DAYS", "30"))
    # X-Forwarded-For solo se usa como IP observada si un proxy autorizado la reescribe (por defecto, no).
    TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "false").strip().lower() == "true"

    # --- Canales externos de notificación (nunca valores por defecto con credenciales) ---
    WHATSAPP_ENABLED = _env_bool("WHATSAPP_ENABLED", "false")
    WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v20.0")
    SMS_ENABLED = _env_bool("SMS_ENABLED", "false")
    SMS_PROVIDER = os.getenv("SMS_PROVIDER", "twilio")
    SMS_ACCOUNT_SID = os.getenv("SMS_ACCOUNT_SID", "")
    SMS_AUTH_TOKEN = os.getenv("SMS_AUTH_TOKEN", "")
    SMS_FROM_NUMBER = os.getenv("SMS_FROM_NUMBER", "")
    # Pruebas: con true los proveedores externos no hacen red y registran el envío en memoria.
    NOTIFICATIONS_DRY_RUN = _env_bool("NOTIFICATIONS_DRY_RUN", "false")

    # --- Google Drive para respaldos (credenciales OAuth dedicadas, distintas del login OIDC) ---
    GOOGLE_DRIVE_CLIENT_ID = os.getenv("GOOGLE_DRIVE_CLIENT_ID")
    GOOGLE_DRIVE_CLIENT_SECRET = os.getenv("GOOGLE_DRIVE_CLIENT_SECRET")
    GOOGLE_DRIVE_REDIRECT_URI = os.getenv("GOOGLE_DRIVE_REDIRECT_URI")

    # Nombre de la tarea del Programador de tareas de Windows para el respaldo automático.
    BACKUP_TASK_NAME = os.getenv("BACKUP_TASK_NAME", "FerreteriaConejo-RespaldoAutomatico")
