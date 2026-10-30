"""One explicit disposable database; never use a URL from an ini or dotenv."""
from alembic import context
from sqlalchemy import create_engine
from docker_support.guard import validate

validate()
from app.config import Config

engine = create_engine(Config.SQLALCHEMY_DATABASE_URI, pool_pre_ping=True, hide_parameters=True)
with engine.connect() as connection:
    if not connection.dialect.is_mariadb:
        raise RuntimeError("MariaDB required")
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
