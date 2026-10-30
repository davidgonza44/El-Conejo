"""Controlador de gestión de usuarios: traduce HTTP <-> servicio."""
from flask import jsonify, request

from app.services import user_service
from app.services.exceptions import ValidationError


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        raise ValidationError("El cuerpo de la petición debe ser un JSON válido.")
    return data


def list_users():
    users = user_service.list_users()
    return jsonify({"count": len(users), "items": [u.to_dict() for u in users]})


def get_user(user_id: int):
    user = user_service.get_user_or_404(user_id)
    return jsonify(user.to_dict())


def create_user():
    user = user_service.create_user(_json_body())
    return jsonify({"message": "Usuario creado correctamente.", "user": user.to_dict()}), 201


def update_user(user_id: int):
    user = user_service.update_user(user_id, _json_body())
    return jsonify({"message": "Usuario actualizado correctamente.", "user": user.to_dict()})
