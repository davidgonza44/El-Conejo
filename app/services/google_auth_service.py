"""Login con Google mediante OpenID Connect (Authlib).

Google solo se usa para autenticar la identidad del usuario (scopes mínimos:
openid, email, profile). No se accede a Gmail, Drive ni otros servicios y no
se persisten los tokens de Google.
"""
import re

from flask import current_app

from app.extensions import db, oauth
from app.models import AuthIdentity, User
from app.models.auth_identity import PROVIDER_GOOGLE
from app.models.user import ROLE_VENDEDOR
from app.services import auth_flow_service, login_event_service
from app.services.exceptions import ApiError, ValidationError

_USERNAME_SANITIZE_RE = re.compile(r"[^a-z0-9_.-]")

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"


def register_oauth_client(app) -> None:
    """Registra el cliente 'google' en Authlib (se llama desde create_app)."""
    oauth.register(
        name="google",
        client_id=app.config.get("GOOGLE_CLIENT_ID"),
        client_secret=app.config.get("GOOGLE_CLIENT_SECRET"),
        server_metadata_url=GOOGLE_DISCOVERY_URL,
        client_kwargs={"scope": "openid email profile"},
    )


def ensure_configured() -> None:
    """Error claro si faltan credenciales de Google en el .env."""
    missing = [
        name
        for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI")
        if not current_app.config.get(name)
    ]
    if missing:
        raise ApiError(
            "Login con Google no configurado. Faltan variables en .env: "
            f"{', '.join(missing)}. Cree las credenciales OAuth en Google "
            "Cloud Console y reinicie el servidor.",
            status_code=503,
        )


def _generate_unique_username(email: str) -> str:
    """Deriva un username del email y evita duplicados con un sufijo numérico."""
    base = _USERNAME_SANITIZE_RE.sub("", email.split("@")[0].lower())[:40] or "usuario"
    if len(base) < 3:
        base = f"{base}user"

    candidate = base
    suffix = 1
    while User.query.filter_by(username=candidate).first() is not None:
        suffix += 1
        candidate = f"{base}{suffix}"
    return candidate


def login_from_userinfo(userinfo: dict) -> tuple[User, str, dict]:
    """Resuelve el usuario a partir del userinfo OIDC validado y cierra el login.

    Devuelve (usuario, acción, resultado) donde acción es 'login', 'linked' o 'created' y
    resultado es el de auth_flow_service (sesión abierta o desafío 2FA pendiente).
    """
    sub = userinfo.get("sub")
    email = (userinfo.get("email") or "").strip().lower()
    if not sub or not email:
        raise ValidationError("Google no devolvió la información mínima (sub y email).")
    if userinfo.get("email_verified") is not True:
        raise ValidationError("Google debe verificar el correo electrónico antes de iniciar sesión.", status_code=401)

    # Caso 1: la identidad de Google ya existe -> iniciar sesión directa.
    identity = AuthIdentity.query.filter_by(
        provider=PROVIDER_GOOGLE, provider_user_id=str(sub)
    ).first()
    if identity is not None:
        user = identity.user
        action = "login"
    else:
        user = User.query.filter(db.func.lower(User.email) == email).first()
        if user is not None:
            # A locally edited address is not proof of ownership and cannot link
            # a different Google subject to an existing account automatically.
            other_google = AuthIdentity.query.filter_by(user_id=user.id, provider=PROVIDER_GOOGLE).first()
            if other_google is not None:
                raise ValidationError("Esta cuenta ya tiene una identidad Google vinculada. Use su método habitual de acceso.", status_code=403)
            if not user.email_verified:
                raise ValidationError("Inicie sesión con su método habitual y verifique el correo de la cuenta antes de vincular Google.", status_code=403)
            # Caso 2: usuario existente por email -> vincular identidad Google.
            action = "linked"
        else:
            # Caso 3: usuario nuevo -> crearlo con rol de menor privilegio.
            user = User(
                name=(userinfo.get("name") or email.split("@")[0]).strip(),
                email=email,
                username=_generate_unique_username(email),
                role=ROLE_VENDEDOR,
                email_verified=True,
                password_hash=None,
            )
            db.session.add(user)
            db.session.flush()
            action = "created"

        db.session.add(
            AuthIdentity(
                user_id=user.id,
                provider=PROVIDER_GOOGLE,
                provider_user_id=str(sub),
                email=email,
            )
        )

    if not user.is_active:
        db.session.rollback()
        login_event_service.record(email, False, reason="inactive", user=user, provider="google")
        raise ValidationError(
            "El usuario está inactivo. Contacte al administrador.", status_code=401
        )

    # Google verifica el email; se refleja en el usuario local.
    if userinfo.get("email_verified") and email == user.email.lower() and not user.email_verified:
        user.email_verified = True

    db.session.commit()
    return user, action, auth_flow_service.finish_login(user, "google", email)
