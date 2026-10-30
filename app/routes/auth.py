"""Rutas REST de autenticación."""
from flask import Blueprint

from app.controllers import auth_controller
from app.utils.auth_decorators import login_required
from app.utils.csrf_guard import csrf_protected

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

# Existing auth endpoints remain canonical, including avatar and password changes.
from app.controllers import profile_controller
auth_bp.add_url_rule("/me/profile", view_func=login_required(profile_controller.update), methods=["PATCH"])

_me = login_required


def _me_write(view):
    """Mutación de la cuenta propia: sesión obligatoria (401) y luego token CSRF (400)."""
    return login_required(csrf_protected(view))


auth_bp.add_url_rule("/register", view_func=auth_controller.register, methods=["POST"])
auth_bp.add_url_rule("/login", view_func=auth_controller.login, methods=["POST"])
auth_bp.add_url_rule("/logout", view_func=auth_controller.logout, methods=["POST"])
auth_bp.add_url_rule("/me", view_func=auth_controller.me, methods=["GET"])
auth_bp.add_url_rule("/change-password", view_func=_me_write(auth_controller.change_password), methods=["POST"])
auth_bp.add_url_rule(
    "/passwordless/request",
    view_func=auth_controller.passwordless_request,
    methods=["POST"],
)
auth_bp.add_url_rule(
    "/passwordless/verify",
    view_func=auth_controller.passwordless_verify,
    methods=["POST"],
)
auth_bp.add_url_rule(
    "/password-reset/request",
    view_func=auth_controller.password_reset_request,
    methods=["POST"],
)
auth_bp.add_url_rule(
    "/password-reset/confirm",
    view_func=auth_controller.password_reset_confirm,
    methods=["POST"],
)
auth_bp.add_url_rule(
    "/google/login", view_func=auth_controller.google_login, methods=["GET"]
)
auth_bp.add_url_rule(
    "/google/callback", view_func=auth_controller.google_callback, methods=["GET"]
)

# Verificación en dos pasos (TOTP): desafío durante el login (sin sesión) y gestión propia (con sesión).
auth_bp.add_url_rule("/mfa/challenge", view_func=auth_controller.mfa_challenge, methods=["GET"])
auth_bp.add_url_rule("/mfa/verify", view_func=auth_controller.mfa_verify, methods=["POST"])
auth_bp.add_url_rule("/mfa", view_func=_me(auth_controller.mfa_status), methods=["GET"])
auth_bp.add_url_rule("/mfa/enroll", view_func=_me_write(auth_controller.mfa_enroll), methods=["POST"])
auth_bp.add_url_rule("/mfa/confirm", view_func=_me_write(auth_controller.mfa_confirm), methods=["POST"])
auth_bp.add_url_rule("/mfa/disable", view_func=_me_write(auth_controller.mfa_disable), methods=["POST"])
auth_bp.add_url_rule("/mfa/recovery-codes", view_func=_me_write(auth_controller.mfa_recovery_codes), methods=["POST"])
auth_bp.add_url_rule("/mfa/devices", view_func=_me(auth_controller.trusted_devices), methods=["GET"])
auth_bp.add_url_rule("/mfa/devices", view_func=_me_write(auth_controller.add_trusted_device), methods=["POST"])
auth_bp.add_url_rule("/mfa/devices", view_func=_me_write(auth_controller.revoke_trusted_devices), methods=["DELETE"])
auth_bp.add_url_rule("/mfa/devices/<int:device_id>", view_func=_me_write(auth_controller.revoke_trusted_device),
                     methods=["DELETE"])

# Foto de perfil del usuario autenticado (multipart/form-data, campo "image").
auth_bp.add_url_rule(
    "/me/profile-photo",
    view_func=_me(auth_controller.upload_profile_photo),
    methods=["POST"],
)
auth_bp.add_url_rule(
    "/me/profile-photo",
    view_func=_me(auth_controller.delete_profile_photo),
    methods=["DELETE"],
)
