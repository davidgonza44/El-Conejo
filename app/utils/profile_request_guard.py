"""Block preview side effects before session touching; protect account writes with CSRF."""
from urllib.parse import parse_qs, urlsplit
from flask import current_app, jsonify, request
from flask_wtf.csrf import validate_csrf
from wtforms.validators import ValidationError


def is_reference_request():
    return current_app.debug and (request.args.get("ref") == "1" or
        parse_qs(urlsplit(request.referrer or "").query).get("ref", [None])[0] == "1")


def guard():
    if is_reference_request() and (request.method not in ("GET", "HEAD", "OPTIONS") or
                                   request.endpoint == "auth.google_callback"):
        return jsonify(error="La vista de referencia no permite cambios."), 403
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    protected = (request.path in ("/api/auth/me/profile", "/api/auth/me/profile-photo") or
                 request.path.startswith("/api/settings/security/sessions"))
    if protected:
        request.max_content_length = 2 * 1024 * 1024 + 65536
        try:
            validate_csrf(request.headers.get("X-CSRFToken"))
        except ValidationError:
            return jsonify(error="Token CSRF inválido o ausente."), 400
    return None
