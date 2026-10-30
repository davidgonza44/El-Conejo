"""Explicit local thesis database/schema preparation; no commercial data or dotenv."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import seed_tesis_safety as safety


def main():
    from flask import Flask
    from sqlalchemy import create_engine, inspect, text

    url = safety.configured_url()
    safety.require(os.getenv('DB_ALLOW_EMPTY_PASSWORD') == '1', 'Se requiere autorización local explícita.')
    server = create_engine(url._replace(database=None), connect_args={'connect_timeout': 5})
    with server.connect() as connection:
        safety.require(connection.execute(text('SELECT DATABASE()')).scalar() is None, 'Conexión inicial inesperada.')
        safety.require(connection.execute(text('SELECT @@port')).scalar() == 3306, 'Puerto efectivo inesperado.')
        exists = connection.execute(text('SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=:name'),
                                    {'name': safety.DATABASE}).scalar()
        if not exists:
            connection.execute(text('CREATE DATABASE `ferreteria_conejo_tesis` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci'))
            connection.commit()
        print('Base de tesis: ' + ('existente' if exists else 'creada'))
    server.dispose()
    domain = safety.load_domain()
    app = Flask('tesis_schema')
    app.config.update(SQLALCHEMY_DATABASE_URI=url, SQLALCHEMY_TRACK_MODIFICATIONS=False)
    domain.db.init_app(app)
    with app.app_context(), domain.db.engine.connect() as connection:
        safety.require(connection.execute(text('SELECT DATABASE()')).scalar() == safety.DATABASE, 'Base efectiva incorrecta.')
        existing = inspect(connection).get_table_names()
        safety.require(not existing, 'La base tiene tablas; revisar antes de preparar, sin modificar el esquema existente.')
        connection.rollback()
        # Same mechanism as init_db.py, without its generic seed or application configuration.
        domain.db.metadata.create_all(connection)
        connection.commit()
        safety.mysql_preflight(connection, prepared=False)
        safety.validate_domain_schema(connection, domain)
        safety.require(set(domain.db.metadata.tables) <= set(inspect(connection).get_table_names()), 'Faltan modelos.')
        print(f'Esquema completo: {len(domain.db.metadata.tables)} tablas de modelos; sin datos comerciales.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except safety.SeedError as exc:
        print(f'ABORTADO: {exc}', file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        print(f'ABORTADO: {type(exc).__name__}; no se muestran datos de conexión.', file=sys.stderr)
        raise SystemExit(1)
