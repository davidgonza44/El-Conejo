"""Añade propuestas separadas exclusivamente en DEMO 5001; conserva las categorías."""
import argparse
import os
import sys
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def apply_changes(connection):
    from sqlalchemy import inspect
    from app.models.category_change import CategoryChange

    inspector = inspect(connection)
    if not {"categories", "users"}.issubset(inspector.get_table_names()):
        raise RuntimeError("Se requiere el esquema de categorías y usuarios.")
    CategoryChange.__table__.create(connection, checkfirst=True)
    if {column["name"] for column in inspect(connection).get_columns("category_changes")} != set(CategoryChange.__table__.columns.keys()):
        raise RuntimeError("Esquema de propuestas inesperado; se requiere revisión manual.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    if not parser.parse_args().apply:
        print("Sin cambios. --apply requiere el contenedor exclusivo de DEMO 5001.")
        return
    from sqlalchemy import text
    from docker_support.guard import validate_demo, validate_demo_url
    validate_demo()
    from docker_support.bootstrap import engine
    from docker_support.demo import require_ready

    target = engine()
    validate_demo_url(target.url)
    try:
        with target.begin() as connection:
            require_ready(connection)
            if connection.execute(text("SELECT GET_LOCK('conejo.demo.category_changes',10)")).scalar_one() != 1:
                raise RuntimeError("Otra migración está en curso.")
            try:
                apply_changes(connection)
            finally:
                connection.execute(text("SELECT RELEASE_LOCK('conejo.demo.category_changes')"))
    finally:
        target.dispose()
    print("PASS DEMO 5001: propuestas disponibles; categorías, productos y permisos conservados.")


if __name__ == "__main__":
    main()
