"""Configuración persistida de la aplicación (clave tipada -> valor JSON)."""
from datetime import datetime, timezone

from app.extensions import db


class AppSetting(db.Model):
    __tablename__ = "app_settings"

    setting_key = db.Column(db.String(64), primary_key=True)
    value_json = db.Column(db.JSON, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
