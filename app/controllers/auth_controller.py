"""Controlador de autenticación: traduce HTTP <-> servicio."""
from flask import current_app, jsonify, make_response, redirect, request, url_for
from flask_login import current_user

from app.extensions import oauth
from app.services import (
    auth_service,
    google_auth_service,
    mfa_service,
    password_reset_service,
    passwordless_service,
)
from app.services.exceptions import ValidationError
from app.utils.navigation import home_path


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        raise ValidationError("El cuerpo de la petición debe ser un JSON válido.")
    return data


def _login_response(result: dict, message: str | None = None):
    """Sesión abierta => usuario; 2FA pendiente => {"mfa_required": true} sin sesión."""
    if result.get("mfa_required"):
        return jsonify({"mfa_required": True, "message": "Ingrese el código de verificación en dos pasos."})
    user = result["user"]
    return jsonify({"message": message or f"Bienvenido, {user['name']}.", "user": user,
                    "password_expired": result.get("password_expired", False),
                    "home": home_path(user.get("role") or "")})


def register():
    user = auth_service.register(_json_body())
    return jsonify({"message": "Usuario registrado correctamente.", "user": user.to_dict()}), 201


def login():
    return _login_response(auth_service.login(_json_body()))


def logout():
    auth_service.logout()
    return jsonify({"message": "Sesión cerrada correctamente."})


def change_password():
    auth_service.change_password(current_user, _json_body())
    return jsonify({"message": "Contraseña actualizada correctamente."})


def me():
    if not current_user.is_authenticated:
        return jsonify({"error": "No hay sesión activa."}), 401
    return jsonify(current_user.to_dict())


def upload_profile_photo():
    from app.services import profile_photo_service

    user = profile_photo_service.set_photo(current_user, request.files.get("image"))
    return jsonify(profile_photo_service.photo_response(user))


def delete_profile_photo():
    from app.services import profile_photo_service

    user = profile_photo_service.remove_photo(current_user)
    return jsonify(profile_photo_service.remove_response(user))


def passwordless_request():
    response = passwordless_service.request_token(_json_body())
    return jsonify(response)


def passwordless_verify():
    return _login_response(passwordless_service.verify(_json_body()))


def password_reset_request():
    response = password_reset_service.request_reset(_json_body())
    return jsonify(response)


def password_reset_confirm():
    response = password_reset_service.confirm_reset(_json_body())
    return jsonify(response)


def google_login():
    """Redirige al consentimiento de Google (flujo Authorization Code + OIDC)."""
    google_auth_service.ensure_configured()
    redirect_uri = current_app.config["GOOGLE_REDIRECT_URI"]
    return oauth.google.authorize_redirect(redirect_uri)


def google_callback():
    """Recibe el code de Google, valida el id_token e inicia la sesión (o el desafío 2FA)."""
    google_auth_service.ensure_configured()
    # authorize_access_token valida firma, audiencia, emisor, expiración y
    # nonce del id_token según la metadata OIDC de Google.
    token = oauth.google.authorize_access_token()
    userinfo = token.get("userinfo")
    if not userinfo:
        raise ValidationError("Google no devolvió el id_token esperado.", status_code=401)

    user, action, result = google_auth_service.login_from_userinfo(dict(userinfo))
    if result.get("mfa_required"):
        return redirect(url_for("pages.login", mfa=1))
    messages = {
        "login": f"Bienvenido, {user.name}.",
        "linked": f"Cuenta de Google vinculada. Bienvenido, {user.name}.",
        "created": f"Usuario creado con Google. Bienvenido, {user.name}.",
    }
    return jsonify({"message": messages[action], "action": action, "user": user.to_dict()})


# ---- 2FA (TOTP) ----
def mfa_challenge():
    """¿Hay un desafío 2FA pendiente en esta sesión? (la página de login lo consulta tras Google)."""
    return jsonify({"pending": mfa_service.pending_challenge() is not None})


def mfa_verify():
    user, device_token = mfa_service.complete_challenge(_json_body())
    response = make_response(jsonify({"message": f"Bienvenido, {user.name}.", "user": user.to_dict(),
                                      "home": home_path(user.role)}))
    if device_token:
        response.set_cookie(mfa_service.TRUSTED_COOKIE, device_token, **mfa_service.cookie_kwargs())
    return response


def mfa_status():
    return jsonify(mfa_service.status(current_user))


def mfa_enroll():
    # Sin cuerpo para el primer enrolamiento; con 2FA activo el cuerpo trae code y current_password.
    data = request.get_json(silent=True)
    return jsonify(mfa_service.start_enrollment(current_user, data if isinstance(data, dict) else {}))


def mfa_confirm():
    result = mfa_service.confirm_enrollment(current_user, _json_body())
    message = "Factor 2FA reemplazado." if result["replaced"] else "Verificación en dos pasos activada."
    return jsonify({"message": message, **result})


def mfa_disable():
    mfa_service.disable(current_user, _json_body())
    response = make_response(jsonify({"message": "Verificación en dos pasos desactivada."}))
    response.delete_cookie(mfa_service.TRUSTED_COOKIE)
    return response


def mfa_recovery_codes():
    return jsonify({"message": "Códigos regenerados. Guárdelos: no volverán a mostrarse.",
                    **mfa_service.regenerate_recovery_codes(current_user, _json_body())})


def trusted_devices():
    return jsonify(mfa_service.list_trusted_devices(current_user))


def add_trusted_device():
    token = mfa_service.add_trusted_device(current_user, _json_body())
    response = make_response(jsonify({"message": "Este dispositivo ahora es confiable.",
                                      **mfa_service.list_trusted_devices(current_user)}))
    response.set_cookie(mfa_service.TRUSTED_COOKIE, token, **mfa_service.cookie_kwargs())
    return response


def revoke_trusted_device(device_id: int):
    mfa_service.revoke_trusted_device(current_user, device_id)
    return jsonify({"message": "Dispositivo revocado.", **mfa_service.list_trusted_devices(current_user)})


def revoke_trusted_devices():
    count = mfa_service.revoke_all_trusted_devices(current_user)
    response = make_response(jsonify({"message": f"Dispositivos revocados: {count}.", "items": [], "available": True}))
    response.delete_cookie(mfa_service.TRUSTED_COOKIE)
    return response
