"""Lógica de negocio de gestión de usuarios (permiso users:manage)."""
from flask_login import current_user

from app.extensions import db
from app.models import User
from app.services import auth_service, password_policy_service, rbac_service
from app.services.exceptions import NotFoundError, ValidationError


def get_user_or_404(user_id: int) -> User:
    user = db.session.get(User, user_id)
    if user is None:
        raise NotFoundError(f"El usuario con id {user_id} no existe.")
    return user


def list_users() -> list[User]:
    return User.query.order_by(User.name).all()


def create_user(data: dict) -> User:
    """Un admin crea un usuario; reutiliza las validaciones del registro con cualquier rol activo."""
    user = auth_service.register(data, allowed_roles=None)
    _notify(f"Usuario creado: {user.name}", f"Se creó la cuenta {user.username} con rol {user.role}.")
    return user


def update_user(user_id: int, data: dict) -> User:
    user = get_user_or_404(user_id)
    changes = []

    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            raise ValidationError("El campo 'name' no puede estar vacío.")
        user.name = name

    if "role" in data:
        role = rbac_service.validate_role_slug(data.get("role"))
        if user.id == current_user.id and role != user.role:
            raise ValidationError("No puede cambiar su propio rol.")
        if role != user.role:
            rbac_service.guard_last_admin(user)
            changes.append(f"rol {user.role} → {role}")
        user.role = role

    if "is_active" in data:
        is_active = bool(data.get("is_active"))
        if user.id == current_user.id and not is_active:
            raise ValidationError("No puede desactivar su propia cuenta.")
        if not is_active and user.is_active:
            rbac_service.guard_last_admin(user)
        if is_active != user.is_active:
            changes.append("activado" if is_active else "desactivado")
        user.is_active = is_active

    if "password" in data:
        password_policy_service.apply(user, auth_service.validate_password(data.get("password"), user))
        changes.append("contraseña restablecida")

    db.session.commit()
    if changes:
        _notify(f"Usuario modificado: {user.name}", f"{user.username}: {', '.join(changes)}.")
    return user


def _notify(title: str, message: str) -> None:
    from app.services import notification_service

    notification_service.security_event(title, message, actor=current_user, dedupe_key=None)
