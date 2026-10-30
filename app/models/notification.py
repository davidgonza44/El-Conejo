"""Bandeja persistente de notificaciones y registro de entregas por canal externo."""
from datetime import datetime

from app.extensions import db

EVENT_LOW_STOCK = "low_stock"
EVENT_REPLENISHMENT = "replenishment"
EVENT_SALES = "sales"
EVENT_CRITICAL = "critical"
EVENT_MAINTENANCE = "maintenance"
EVENT_SECURITY = "security"
EVENT_RECONCILIATION = "reconciliation"
EVENTS = (EVENT_LOW_STOCK, EVENT_REPLENISHMENT, EVENT_SALES, EVENT_CRITICAL, EVENT_MAINTENANCE, EVENT_SECURITY)
EVENT_LABELS = {
    EVENT_LOW_STOCK: "Stock bajo",
    EVENT_REPLENISHMENT: "Órdenes de compra (reabastecimiento)",
    EVENT_SALES: "Nueva venta / pedido",
    EVENT_CRITICAL: "Productos críticos",
    EVENT_MAINTENANCE: "Mantenimiento del sistema",
    EVENT_SECURITY: "Eventos de seguridad",
    EVENT_RECONCILIATION: "Conciliación de inventario",
}

CHANNEL_APP = "app"
CHANNEL_EMAIL = "email"
CHANNEL_WHATSAPP = "whatsapp"
CHANNEL_SMS = "sms"
CHANNELS = (CHANNEL_APP, CHANNEL_EMAIL, CHANNEL_WHATSAPP, CHANNEL_SMS)
EXTERNAL_CHANNELS = (CHANNEL_EMAIL, CHANNEL_WHATSAPP, CHANNEL_SMS)

SEVERITIES = ("info", "success", "warning", "critical")


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    event = db.Column(db.String(20), nullable=False, index=True)
    severity = db.Column(db.String(10), nullable=False, default="info")
    title = db.Column(db.String(160), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    entity_type = db.Column(db.String(30), nullable=True)
    entity_id = db.Column(db.Integer, nullable=True)
    href = db.Column(db.String(255), nullable=True)
    dedupe_key = db.Column(db.String(120), nullable=True, index=True)
    read_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    deliveries = db.relationship("NotificationDelivery", back_populates="notification", lazy="selectin",
                                 cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "event": self.event,
            "event_label": EVENT_LABELS.get(self.event, self.event),
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "href": self.href,
            "read": self.read_at is not None,
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "created_at": self.created_at.isoformat(),
            "channels": ["app"] + sorted({d.channel for d in self.deliveries if d.status == "sent"}),
        }


class NotificationDelivery(db.Model):
    __tablename__ = "notification_deliveries"

    id = db.Column(db.Integer, primary_key=True)
    notification_id = db.Column(db.Integer, db.ForeignKey("notifications.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    channel = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(10), nullable=False)  # sent | failed | queued
    detail = db.Column(db.String(255), nullable=True)  # mensaje seguro (sin credenciales)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    notification = db.relationship("Notification", back_populates="deliveries")
