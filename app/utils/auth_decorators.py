"""Decoradores de autenticación y autorización para las rutas de la API."""
from functools import wraps

from flask import jsonify
from flask_login import current_user
from flask_login import login_required as _flask_login_required

from app.utils.permissions import role_has_permission

# Reexportado: el manejador unauthorized de la app lo convierte en JSON 401.
login_required = _flask_login_required


def role_required(*roles: str):
    """Permite el acceso solo a los roles indicados (401 sin sesión, 403 sin rol)."""

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return jsonify({"error": "Autenticación requerida."}), 401
            if current_user.role not in roles:
                return (
                    jsonify(
                        {
                            "error": (
                                f"Acceso denegado: se requiere rol "
                                f"{' o '.join(roles)}."
                            )
                        }
                    ),
                    403,
                )
            return view(*args, **kwargs)

        return wrapper

    return decorator


def permission_required(permission: str):
    """Permite el acceso solo a roles que tengan el permiso indicado.

    Las APIs usan este decorador y responden JSON. No depende del menú.
    """

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return jsonify({"error": "Autenticación requerida."}), 401
            if not role_has_permission(current_user.role, permission):
                return (
                    jsonify(
                        {"error": f"Acceso denegado: falta el permiso '{permission}'."}
                    ),
                    403,
                )
            return view(*args, **kwargs)

        return wrapper

    return decorator


def forbidden_page():
    """Página HTML de acceso denegado. No sustituye el 403 JSON de las APIs."""
    from flask import render_template

    return render_template("access_denied.html"), 403


def require_page_permission(*permissions: str):
    """None si el usuario puede ver la página; si no, la plantilla con estado 403."""
    if all(role_has_permission(current_user.role, permission) for permission in permissions):
        return None
    return forbidden_page()


def require_page_roles(*roles: str):
    """Inicio no tiene permiso granular: lo autorizan estos slugs, igual que su API."""
    if current_user.role in roles:
        return None
    return forbidden_page()


def page_permission_required(permission: str):
    """Guard de una página HTML. La API del mismo módulo sigue usando permission_required."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(*args, **kwargs):
            denied = require_page_permission(permission)
            if denied is not None:
                return denied
            return view(*args, **kwargs)

        return wrapper

    return decorator
