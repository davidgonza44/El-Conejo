from datetime import datetime

from app.extensions import db


class PasswordResetToken(db.Model):
    """Token temporal de un solo uso para restablecer la contraseña.

    Solo se almacena el hash SHA-256 del token; el token en texto plano viaja
    únicamente dentro del enlace enviado por correo.
    """

    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True, index=True
    )
    email = db.Column(db.String(120), nullable=False, index=True)
    token_hash = db.Column(db.String(255), nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    used_at = db.Column(db.DateTime, nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    # Datos de auditoría de la solicitud (opcionales).
    request_ip = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    def __repr__(self) -> str:
        return f"<PasswordResetToken {self.email}>"
