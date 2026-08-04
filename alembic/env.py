from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from knowledge.config.settings import Settings
from knowledge.persistence.schema import metadata


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def _database_url() -> str:
    configured = config.attributes.get("database_url")
    url = str(configured or Settings().resolved_psycopg_url)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _schema() -> str:
    value = str(config.attributes.get("database_schema") or Settings().database_schema)
    if not value.replace("_", "a").isalnum() or value[0].isdigit():
        raise ValueError("Invalid PostgreSQL schema identifier")
    return value


def run_migrations_offline() -> None:
    schema = _schema()
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema=schema,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    schema = _schema()
    engine = create_engine(_database_url(), poolclass=NullPool, pool_pre_ping=True)
    with engine.connect() as connection:
        connection.exec_driver_sql(f'SET search_path TO "{schema}", public')
        # SET opens an implicit transaction in SQLAlchemy 2.x. Commit it before
        # Alembic starts its own transactional-DDL boundary, otherwise closing
        # the connection rolls the whole migration back.
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table_schema=schema,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
