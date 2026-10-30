"""Añade el registro de propuestas exclusivamente en DEMO 5001; no cambia productos."""
import argparse
import os
import sys
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def apply_changes(connection):
    from sqlalchemy import inspect
    from app.models.product_change import ProductChange

    if not {"products", "users", "product_state_histories"}.issubset(inspect(connection).get_table_names()):
        raise RuntimeError("Se requiere el esquema de productos, usuarios e historial de estados.")
    ProductChange.__table__.create(connection, checkfirst=True)


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
            if connection.execute(text("SELECT GET_LOCK('conejo.demo.product_changes',10)")).scalar_one() != 1:
                raise RuntimeError("Otra migración está en curso.")
            try:
                apply_changes(connection)
            finally:
                connection.execute(text("SELECT RELEASE_LOCK('conejo.demo.product_changes')"))
    finally:
        target.dispose()
    print("PASS DEMO 5001: registro de propuestas disponible; productos y permisos conservados.")


if __name__ == "__main__":
    main()
