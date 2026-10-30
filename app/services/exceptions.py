"""Excepciones de negocio que se traducen a respuestas JSON de la API."""


class ApiError(Exception):
    status_code = 400

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class ValidationError(ApiError):
    """Datos de entrada inválidos (400)."""
    status_code = 400


class NotFoundError(ApiError):
    """Recurso inexistente (404)."""
    status_code = 404


class ConflictError(ApiError):
    """Conflicto con el estado actual, p. ej. código duplicado (409)."""
    status_code = 409
