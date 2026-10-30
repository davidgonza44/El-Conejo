"""Modelos de seguridad de cuenta: historial de contraseñas, sesiones, eventos de login,
2FA (TOTP), códigos de recuperación y dispositivos confiables.

Nunca se guardan tokens reutilizables ni secretos en claro: solo hashes SHA-256 o texto cifrado.
"""
from datetime import datetime

from app.extensions import db


class PasswordHistory(db.Model):
    __tablename__ = "password_history"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class UserSession(db.Model):
    __tablename__ = "user_sessions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    device_label = db.Column(db.String(80), nullable=True)
    provider = db.Column(db.String(20), nullable=False, default="local")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    revoked_at = db.Column(db.DateTime, nullable=True)

    @property
    def is_valid(self) -> bool:
        return self.revoked_at is None and self.expires_at > datetime.utcnow()

    def to_dict(self, current: bool = False, inactive_after_hours: int = 24) -> dict:
        idle = (datetime.utcnow() - self.last_seen_at).total_seconds() / 3600
        return {
            "id": self.id,
            "device": self.device_label or "Dispositivo desconocido",
            "ip_address": self.ip_address or "No disponible",
            "location": "No disponible",
            "provider": self.provider,
            "created_at": self.created_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "current": current,
            "status": "active" if current or idle < inactive_after_hours else "inactive",
        }


class LoginEvent(db.Model):
    __tablename__ = "login_events"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    identifier = db.Column(db.String(120), nullable=False)
    provider = db.Column(db.String(20), nullable=False, default="local")
    success = db.Column(db.Boolean, nullable=False)
    reason = db.Column(db.String(40), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    device_label = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "identifier": self.identifier,
            "provider": self.provider,
            "success": self.success,
            "reason": self.reason,
            "ip_address": self.ip_address or "No disponible",
            "device": self.device_label or "Dispositivo desconocido",
            "location": "No disponible",
            "created_at": self.created_at.isoformat(),
        }


class UserMfa(db.Model):
    __tablename__ = "user_mfa"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    secret_encrypted = db.Column(db.Text, nullable=True)
    pending_secret_encrypted = db.Column(db.Text, nullable=True)
    enabled_at = db.Column(db.DateTime, nullable=True)
    # Último intervalo TOTP aceptado: impide reutilizar el mismo código dentro de su ventana.
    last_verified_step = db.Column(db.BigInteger, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def enabled(self) -> bool:
        return self.enabled_at is not None and bool(self.secret_encrypted)


class MfaRecoveryCode(db.Model):
    __tablename__ = "mfa_recovery_codes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    code_hash = db.Column(db.String(64), nullable=False, unique=True)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class TrustedDevice(db.Model):
    __tablename__ = "trusted_devices"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    label = db.Column(db.String(80), nullable=False)
    user_agent = db.Column(db.String(255), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_used_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    revoked_at = db.Column(db.DateTime, nullable=True)

    @property
    def is_valid(self) -> bool:
        return self.revoked_at is None and self.expires_at > datetime.utcnow()

    def to_dict(self, current: bool = False) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "ip_address": self.ip_address or "No disponible",
            "created_at": self.created_at.isoformat(),
            "last_used_at": self.last_used_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "current": current,
        }
