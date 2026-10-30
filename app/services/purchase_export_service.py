"""Límite común aplicado antes de materializar y formatear exportaciones de Compras."""
from app.services.exceptions import ValidationError
MAX_EXPORT_ROWS = 10_000


def bounded_rows(query):
    rows = query.limit(MAX_EXPORT_ROWS+1).all()
    if len(rows) > MAX_EXPORT_ROWS:
        raise ValidationError(f"La exportación supera {MAX_EXPORT_ROWS} registros. Aplique filtros para reducirla.")
    return rows
