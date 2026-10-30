from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager

ROLE_ADMIN = "admin"
ROLE_INVENTARIO = "inventario"
ROLE_ASISTENTE = "asistente"
ROLE_VENDEDOR = "vendedor"
ROLE_SOPORTE_TI = "soporte_ti"

ROLES = (ROLE_ADMIN, ROLE_INVENTARIO, ROLE_ASISTENTE, ROLE_VENDEDOR, ROLE_SOPORTE_TI)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    # Nullable: usuarios passwordless o de Google pueden no tener contraseña.
    password_hash = db.Column(db.String(255), nullable=True)
    role = db.Column(db.String(20), nullable=False, default=ROLE_VENDEDOR)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    profile_photo_filename = db.Column(db.String(255), nullable=True)
    # Deferred so pending additive migration does not break authentication queries.
    phone = db.deferred(db.Column(db.String(40), nullable=True))
    job_title = db.deferred(db.Column(db.String(120), nullable=True))
    bio = db.deferred(db.Column(db.String(300), nullable=True))
    # Último acceso real (login local, passwordless o Google). NULL = nunca ha iniciado sesión.
    last_login_at = db.Column(db.DateTime, nullable=True)
    # Fecha del último cambio de contraseña; base para la expiración configurable.
    password_changed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    stock_movements = db.relationship("StockMovement", back_populates="user")
    product_state_changes = db.relationship("ProductStateHistory", back_populates="changed_by")
    delivery_notes = db.relationship(
        "DeliveryNote",
        back_populates="creator",
        foreign_keys="DeliveryNote.created_by_user_id",
    )
    identities = db.relationship(
        "AuthIdentity", back_populates="user", cascade="all, delete-orphan"
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def profile_photo_url(self) -> str | None:
        if self.profile_photo_filename:
            return f"/media/users/{self.profile_photo_filename}"
        return None

    def to_dict(self) -> dict:
        """Representación pública del usuario. Nunca incluye password_hash."""
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "username": self.username,
            "role": self.role,
            "is_active": self.is_active,
            "email_verified": self.email_verified,
            "profile_photo_url": self.profile_photo_url,
            "has_password": bool(self.password_hash),
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<User {self.username}>"


@login_manager.user_loader
def load_user(user_id: str):
    """Una cuenta desactivada deja de tener sesión válida en la siguiente petición."""
    user = db.session.get(User, int(user_id))
    return user if user is not None and user.is_active else None
