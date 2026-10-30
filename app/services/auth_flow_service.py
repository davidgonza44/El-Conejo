"""Cierre común de los tres métodos de autenticación (local, passwordless, Google).

La autenticación primaria por sí sola NO establece la sesión cuando el usuario tiene 2FA: se
guarda un desafío y el cliente debe verificar el código en /api/auth/mfa/verify.
"""
from app.models import User
from app.services import login_event_service, mfa_service, password_policy_service, session_service


def finish_login(user: User, provider: str, identifier: str) -> dict:
    """Devuelve {"mfa_required": True} o {"user": ..., "password_expired": bool} con la sesión abierta."""
    if mfa_service.requires_challenge(user):
        mfa_service.begin_challenge(user, provider)  # el resultado 2FA se registra al verificar el código
        return {"mfa_required": True}
    session_service.open_session(user, provider=provider)
    login_event_service.record(identifier, True, reason="ok", user=user, provider=provider)
    return {"user": user.to_dict(), "password_expired": password_policy_service.is_expired(user)}
