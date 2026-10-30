"""Controlador HTTP del chatbot interno."""
from flask import current_app, jsonify, request
from flask_login import current_user

from app.services import chatbot_service
from app.services.exceptions import ApiError, ValidationError


MAX_MESSAGE_LENGTH = 500


def _validated_message() -> str:
    """Valida Content-Type y extrae únicamente el campo ``message``."""
    if request.mimetype != "application/json":
        raise ValidationError(
            "El Content-Type de la petición debe ser application/json."
        )

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("El cuerpo de la petición debe ser un objeto JSON válido.")

    message = data.get("message")
    if not isinstance(message, str):
        raise ValidationError("El campo 'message' es obligatorio y debe ser texto.")
    if len(message) > MAX_MESSAGE_LENGTH:
        raise ValidationError(
            f"El campo 'message' no puede superar {MAX_MESSAGE_LENGTH} caracteres."
        )

    message = message.strip()
    if not message:
        raise ValidationError("El campo 'message' no puede estar vacío.")
    return message


def post_message():
    """Procesa un mensaje autenticado y devuelve siempre una respuesta JSON."""
    try:
        message = _validated_message()
        response = chatbot_service.process_message(message, current_user.role)
        return jsonify(response)
    except ApiError:
        raise
    except Exception:  # noqa: BLE001 - evita filtrar detalles internos al cliente
        current_app.logger.exception("Error interno al procesar un mensaje del chatbot.")
        return jsonify({"error": "Error interno del servidor."}), 500
