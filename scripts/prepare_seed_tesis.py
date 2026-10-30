"""Prepara SOLO seed_manifests en la base de tesis existente. No crea la base ni ejecuta el seed."""
import sys

import seed_tesis_safety as safety


def prepare():
    url = safety.configured_url()
    from sqlalchemy import create_engine

    engine = create_engine(url, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection, safety.locked(connection):
            safety.mysql_preflight(connection, prepared=False)
            created = prepare_registry(connection)
            print("Tabla interna preparada. No se generaron datos comerciales." if created
                  else "Tabla interna ya preparada; no se modificó.")
    finally:
        engine.dispose()


def prepare_registry(connection):
    """Standalone DDL after the CLI has checked the target; also testable on disposable SQLite."""
    from sqlalchemy import inspect

    if "seed_manifests" in inspect(connection).get_table_names():
        safety.validate_registry_schema(connection)
        return False
    connection.commit()
    safety.manifest_table().create(connection)  # Explicit standalone DDL; MySQL implicit commit.
    connection.commit()
    safety.validate_registry_schema(connection)
    return True


def main():
    if len(sys.argv) != 1:
        print("Uso: python scripts/prepare_seed_tesis.py", file=sys.stderr)
        return 1
    try:
        prepare()
    except safety.SeedError as exc:
        print(f"ABORTADO: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("ABORTADO: no se pudo verificar o preparar la tabla interna; no se muestran datos sensibles.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
