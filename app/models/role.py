"""RBAC dinámico: roles, permisos y su relación N:M.

``users.role`` sigue siendo el slug del rol (compatibilidad hacia atrás); los roles del sistema
(admin, inventario, vendedor) se siembran desde ``ROLE_PERMISSIONS`` sin alterar el acceso efectivo.
"""
from datetime import datetime

from app.extensions import db

role_permissions = db.Table(
    "role_permissions",
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    db.Column("permission_id", db.Integer, db.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class Permission(db.Model):
    __tablename__ = "permissions"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(64), nullable=False, unique=True)
    module = db.Column(db.String(40), nullable=False)
    label = db.Column(db.String(120), nullable=False)

    def to_dict(self) -> dict:
        return {"code": self.code, "module": self.module, "label": self.label}


class Role(db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(20), nullable=False, unique=True)
    name = db.Column(db.String(60), nullable=False, unique=True)
    description = db.Column(db.String(255), nullable=True)
    is_system = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    permissions = db.relationship("Permission", secondary=role_permissions, lazy="selectin",
                                  order_by="Permission.code")

    @property
    def permission_codes(self) -> set[str]:
        return {permission.code for permission in self.permissions}

    def to_dict(self, user_count: int | None = None) -> dict:
        data = {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description or "",
            "is_system": self.is_system,
            "is_active": self.is_active,
            "permissions": sorted(self.permission_codes),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if user_count is not None:
            data["user_count"] = user_count
        return data
