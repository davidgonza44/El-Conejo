"""CSRF por cabecera para mutaciones autenticadas (X-CSRFToken del token Flask-WTF de la página).

Se aplica por ruta, no globalmente: login, registro, recuperación y desafío 2FA quedan fuera porque
ocurren sin sesión establecida. Debe envolverse dentro de login_required/permission_required para
conservar 401/403 antes del 400 de CSRF.
"""
from functools import wraps

from flask import jsonify, request
from flask_wtf.csrf import validate_csrf
from wtforms.validators import ValidationError


def csrf_protected(view):
    @wraps(view)
    def protected(*args, **kwargs):
        try:
            validate_csrf(request.headers.get("X-CSRFToken"))
        except ValidationError:
            return jsonify(error="Token CSRF inválido o ausente."), 400
        return view(*args, **kwargs)
    return protected
