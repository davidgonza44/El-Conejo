"""Política de contraseñas configurable (Configuración > Seguridad), historial y expiración.

Se aplica en: alta de usuario, restablecimiento por admin, cambio propio, recuperación por enlace y
"Establecer contraseña". El historial guarda solo hashes; la expiración se evalúa en cada petición.
"""
import re
from datetime import datetime, timedelta

from werkzeug.security import check_password_hash

from app.extensions import db
from app.models import PasswordHistory, User
from app.services import settings_service
from app.services.exceptions import ValidationError
from app.utils.schema import table_ready

_HISTORY_KEEP = 24
_SPECIAL_RE = re.compile(r"[^A-Za-z0-9]")


def policy() -> dict:
    s = settings_service
    return {
        "min_length": s.get(s.PASSWORD_MIN_LENGTH),
        "require_uppercase": s.get(s.PASSWORD_REQUIRE_UPPERCASE),
        "require_lowercase": s.get(s.PASSWORD_REQUIRE_LOWERCASE),
        "require_number": s.get(s.PASSWORD_REQUIRE_NUMBER),
        "require_special": s.get(s.PASSWORD_REQUIRE_SPECIAL),
        "expiration_days": s.get(s.PASSWORD_EXPIRATION_DAYS),
        "history_count": s.get(s.PASSWORD_HISTORY_COUNT),
    }


def _history_ready() -> bool:
    return table_ready(PasswordHistory.__tablename__)


def validate(password, user: User | None = None) -> str:
    """Valida contra la política vigente y, si hay usuario, contra sus últimas N contraseñas."""
    rules = policy()
    password = password or ""
    if len(password) < rules["min_length"]:
        raise ValidationError(f"La contraseña debe tener al menos {rules['min_length']} caracteres.")
    if rules["require_uppercase"] and not re.search(r"[A-ZÁÉÍÓÚÑ]", password):
        raise ValidationError("La contraseña debe incluir al menos una letra mayúscula.")
    if rules["require_lowercase"] and not re.search(r"[a-záéíóúñ]", password):
        raise ValidationError("La contraseña debe incluir al menos una letra minúscula.")
    if rules["require_number"] and not re.search(r"\d", password):
        raise ValidationError("La contraseña debe incluir al menos un número.")
    if rules["require_special"] and not _SPECIAL_RE.search(password):
        raise ValidationError("La contraseña debe incluir al menos un carácter especial.")
    if user is not None and user.id is not None and rules["history_count"] > 0:
        _reject_reused(user, password, rules["history_count"])
    return password


def _reject_reused(user: User, password: str, count: int) -> None:
    hashes = [user.password_hash] if user.password_hash else []
    if _history_ready():
        rows = (PasswordHistory.query.filter_by(user_id=user.id)
                .order_by(PasswordHistory.created_at.desc(), PasswordHistory.id.desc()).limit(count).all())
        hashes.extend(row.password_hash for row in rows)
    for stored in hashes[:count]:
        if check_password_hash(stored, password):
            raise ValidationError(f"No puede reutilizar ninguna de sus últimas {count} contraseñas.")


def apply(user: User, password: str) -> None:
    """Fija la nueva contraseña, registra el hash anterior en el historial y marca la fecha de cambio.

    No hace commit: lo hace el flujo que la invoca dentro de su propia transacción.
    """
    previous = user.password_hash
    user.set_password(password)
    user.password_changed_at = datetime.utcnow()
    if previous and _history_ready() and user.id is not None:
        db.session.add(PasswordHistory(user_id=user.id, password_hash=previous))
        stale = (PasswordHistory.query.filter_by(user_id=user.id)
                 .order_by(PasswordHistory.created_at.desc(), PasswordHistory.id.desc())
                 .offset(_HISTORY_KEEP - 1).all())
        for row in stale:
            db.session.delete(row)


def expires_at(user: User) -> datetime | None:
    """Fecha de expiración o None cuando no aplica (sin contraseña local o expiración desactivada)."""
    days = policy()["expiration_days"]
    if not days or not user.password_hash:
        return None
    base = user.password_changed_at or user.created_at
    return base + timedelta(days=days) if base else None


def is_expired(user: User) -> bool:
    limit = expires_at(user)
    return limit is not None and limit <= datetime.utcnow()
