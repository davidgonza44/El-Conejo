from datetime import datetime

from app.extensions import db

PROVIDER_LOCAL = "local"
PROVIDER_GOOGLE = "google"
PROVIDER_PASSWORDLESS = "passwordless"

PROVIDERS = (PROVIDER_LOCAL, PROVIDER_GOOGLE, PROVIDER_PASSWORDLESS)


class AuthIdentity(db.Model):
    """Identidad de autenticación de un usuario en un proveedor concreto."""

    __tablename__ = "auth_identities"
    __table_args__ = (
        db.UniqueConstraint("provider", "provider_user_id", name="ux_provider_identity"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    provider = db.Column(db.String(20), nullable=False)
    provider_user_id = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", back_populates="identities")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "provider": self.provider,
            "email": self.email,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<AuthIdentity {self.provider}:{self.provider_user_id}>"
