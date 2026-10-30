"""Allowed personal fields only; identity and authorization come from the caller's session."""
import re
from datetime import datetime

from sqlalchemy import inspect, func
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import User, AuthIdentity, PasswordResetToken, PasswordlessToken
from app.services import rbac_service, session_service, login_event_service
from app.services.exceptions import ConflictError, ValidationError
from app.utils.schema import table_ready

FIELDS = {"name": 120, "email": 120, "phone": 40, "job_title": 120, "bio": 300}


def extended_available():
    columns = {c["name"] for c in inspect(db.engine).get_columns("users")}
    return {"phone", "job_title", "bio"} <= columns


def view(user):
    available = extended_available()
    data = {key: getattr(user, key) or "" for key in ("name", "email", "username")}
    data.update({key: (getattr(user, key) or "") if available else "" for key in ("phone", "job_title", "bio")})
    last_login = user.last_login_at
    if not last_login and login_event_service.is_available():
        from app.models import LoginEvent
        last_login = db.session.query(func.max(LoginEvent.created_at)).filter_by(user_id=user.id, success=True).scalar()
    data.update(role=rbac_service.role_labels().get(user.role, rbac_service.SYSTEM_ROLE_NAMES.get(user.role, user.role)), admin=user.is_admin,
                active=user.is_active, created=date(user.created_at), last_login=date(last_login, True),
                avatar=user.profile_photo_url, initials=initials(user.name), extended_available=available)
    return data


def initials(name):
    words = (name or "U").split()
    return (words[0][:1] + words[1][:1] if len(words) > 1 else words[0][:2]).upper()


def date(value, time=False):
    return value.strftime("%d/%m/%Y %H:%M" if time else "%d/%m/%Y") if value else "No disponible"


def update(user, data):
    if not isinstance(data, dict) or set(data) - FIELDS.keys():
        raise ValidationError("Solo se permiten los campos personales del perfil.")
    cleaned = {}
    for key, value in data.items():
        if not isinstance(value, str) or len(value.strip()) > FIELDS[key]:
            raise ValidationError("Revise el formato y la longitud de los campos.")
        cleaned[key] = value.strip()
    if "name" in cleaned and not cleaned["name"]:
        raise ValidationError("El nombre completo es obligatorio.")
    if "email" in cleaned:
        cleaned["email"] = cleaned["email"].lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", cleaned["email"]):
            raise ValidationError("El correo electrónico no es válido.")
        if cleaned["email"] != user.email:
            _change_email(user, cleaned["email"])
    if set(cleaned) & {"phone", "job_title", "bio"} and not extended_available():
        raise ValidationError("Los campos adicionales requieren la migración de perfil.", status_code=503)
    for key, value in cleaned.items():
        setattr(user, key, value)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ConflictError("El correo electrónico ya está en uso.") from exc
    return view(user)


def _change_email(user, email):
    if not session_service.recently_authenticated():
        raise ValidationError("Vuelva a iniciar sesión para cambiar su correo electrónico.", status_code=403)
    if User.query.filter(func.lower(User.email) == email, User.id != user.id).first():
        raise ConflictError("El correo electrónico ya está en uso.")
    # Old emailed links must not grant access after the account address changes.
    for model in (PasswordResetToken, PasswordlessToken):
        if table_ready(model.__tablename__):
            model.query.filter(db.or_(model.user_id == user.id, model.email == user.email),
                               model.used_at.is_(None)).update({"used_at": datetime.utcnow()}, synchronize_session=False)
    user.email_verified = False
    # Local subjects are user IDs; passwordless subjects are verified email addresses.
    # Retire the old address binding, but never rewrite Google's immutable subject.
    AuthIdentity.query.filter_by(user_id=user.id, provider="local").update({"email": email})
    AuthIdentity.query.filter_by(user_id=user.id, provider="passwordless").delete()


def reference():
    return dict(name="Administrador del Sistema", email="admin@ferreteriapro.com", username="admin",
                phone="0412-555-1234", job_title="Administrador", bio="Administrador del sistema Ferretería Pro.",
                role="Administrador", admin=True, active=True, created="15/03/2024", last_login="28/05/2024 10:42",
                avatar=None, initials="AD", extended_available=True)
