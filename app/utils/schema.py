"""Disponibilidad de tablas creadas por migraciones opcionales (estado honesto sin la migración)."""
from app.extensions import db

_ready: set[str] = set()


def table_ready(name: str) -> bool:
    """True cuando la tabla existe. Se consulta information_schema hasta la primera vez que exista."""
    if name not in _ready and db.inspect(db.engine).has_table(name):
        _ready.add(name)
    return name in _ready


def reset_cache() -> None:
    """Solo para pruebas que crean/eliminan esquemas dentro del mismo proceso."""
    _ready.clear()
