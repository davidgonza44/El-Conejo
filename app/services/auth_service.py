"""Lógica de negocio de autenticación local (email/username + contraseña)."""
import re

from app.extensions import db
from app.models import AuthIdentity, User
from app.models.auth_identity import PROVIDER_LOCAL
from app.models.user import ROLE_VENDEDOR
from app.services import auth_flow_service, login_event_service, password_policy_service, rbac_service, session_service
from app.services.exceptions import ConflictError, ValidationError

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,50}$")


def validate_password(password, user: User | None = None) -> str:
    """Política configurable (Configuración > Seguridad) + historial de las últimas N contraseñas."""
    return password_policy_service.validate(password, user)


def register(data: dict, allowed_roles: tuple[str, ...] | None = (ROLE_VENDEDOR,)) -> User:
    """Crea un usuario local. El rol por defecto es 'vendedor' (menor privilegio).

    El registro público solo puede crear 'vendedor'; los demás roles se asignan
    únicamente desde la API protegida de usuarios (allowed_roles=None => cualquier rol activo).
    """
    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationError("El campo 'name' es obligatorio.")

    email = (data.get("email") or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise ValidationError("El campo 'email' no tiene un formato válido.")

    username = (data.get("username") or "").strip().lower()
    if not _USERNAME_RE.match(username):
        raise ValidationError(
            "El campo 'username' debe tener entre 3 y 50 caracteres "
            "(letras, números, punto, guion o guion bajo)."
        )

    password = validate_password(data.get("password"))

    role = rbac_service.validate_role_slug(data.get("role") or ROLE_VENDEDOR)
    if allowed_roles is not None and role not in allowed_roles:
        raise ValidationError(f"No está autorizado para asignar el rol '{role}'.", status_code=403)

    if User.query.filter_by(email=email).first() is not None:
        raise ConflictError(f"Ya existe un usuario con el email '{email}'.")
    if User.query.filter_by(username=username).first() is not None:
        raise ConflictError(f"Ya existe un usuario con el username '{username}'.")

    user = User(name=name, email=email, username=username, role=role)
    password_policy_service.apply(user, password)
    db.session.add(user)
    db.session.flush()  # asigna user.id antes de crear la identidad

    db.session.add(
        AuthIdentity(
            user_id=user.id,
            provider=PROVIDER_LOCAL,
            provider_user_id=str(user.id),
            email=user.email,
        )
    )
    db.session.commit()
    return user


def login(data: dict) -> dict:
    """Autentica con email o username en 'identifier'. Devuelve el resultado de auth_flow_service."""
    identifier = (data.get("identifier") or "").strip().lower()
    password = data.get("password") or ""
    if not identifier or not password:
        raise ValidationError("Los campos 'identifier' y 'password' son obligatorios.")

    user = User.query.filter(
        db.or_(User.email == identifier, User.username == identifier)
    ).first()

    # Mensaje genérico: no revelar si el usuario existe o si falló la contraseña.
    if user is None or not user.check_password(password):
        login_event_service.record(identifier, False, reason="invalid_credentials", user=user)
        raise ValidationError("Credenciales inválidas.", status_code=401)

    if not user.is_active:
        login_event_service.record(identifier, False, reason="inactive", user=user)
        raise ValidationError("El usuario está inactivo. Contacte al administrador.", status_code=401)

    return auth_flow_service.finish_login(user, "local", identifier)


def logout() -> None:
    session_service.close_current()


def change_password(user: User, data: dict) -> None:
    """Cambia (o establece) la contraseña propia. Nunca registra ni devuelve contraseñas.

    - Con contraseña local: exige la actual.
    - Sin contraseña local (Google/passwordless): exige autenticación reciente para "Establecer contraseña".
    """
    if user.password_hash:
        if not user.check_password(data.get("current_password") or ""):
            raise ValidationError("La contraseña actual no es correcta.")
    elif not session_service.recently_authenticated():
        raise ValidationError("Por seguridad, vuelva a iniciar sesión antes de establecer una contraseña.",
                              status_code=403)
    new_password = validate_password(data.get("new_password"), user)
    if new_password != (data.get("confirm_password") or ""):
        raise ValidationError("Las contraseñas no coinciden.")
    if user.check_password(new_password):
        raise ValidationError("La nueva contraseña debe ser distinta de la actual.")
    password_policy_service.apply(user, new_password)
    db.session.commit()
    from app.services import notification_service

    notification_service.security_event("Contraseña actualizada", f"{user.name} cambió su contraseña.",
                                        actor=user, recipients=[user], dedupe_key=None)
