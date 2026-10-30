"""Conexiones OAuth con servicios externos (p. ej. Google Drive para respaldos).

Los tokens se guardan cifrados con la clave de entorno; nunca se devuelven por la API.
"""
from datetime import datetime

from app.extensions import db

PROVIDER_GOOGLE_DRIVE = "google_drive"


class IntegrationConnection(db.Model):
    __tablename__ = "integration_connections"

    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(30), nullable=False, unique=True)
    account_email = db.Column(db.String(120), nullable=True)
    token_encrypted = db.Column(db.Text, nullable=False)
    scope = db.Column(db.String(255), nullable=True)
    remote_folder_id = db.Column(db.String(120), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="connected")  # connected | revoked | error
    status_detail = db.Column(db.String(255), nullable=True)
    connected_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> dict:
        """Estado público: sin tokens."""
        return {
            "provider": self.provider,
            "account_email": self.account_email,
            "status": self.status,
            "status_detail": self.status_detail,
            "connected_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
