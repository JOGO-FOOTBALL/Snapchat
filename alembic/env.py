import os
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy import pool

from alembic import context

from config import Secrets
from models import Base

load_dotenv(override=True)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
MANAGED_SCHEMAS = {"snapchat"}


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name in MANAGED_SCHEMAS
    return True


def get_url() -> str:
    if url := os.environ.get("DATABASE_URL"):
        return url
    env = os.environ.get("ENVIRONMENT", "dev").upper()
    print(env)
    s = Secrets
    return (
        f"postgresql://{getattr(s, f'POSTGRES_USERNAME_DS_{env}')}:{getattr(s, f'POSTGRES_PASSWORD_DS_{env}')}"
        f"@{getattr(s, f'POSTGRES_SERVER_DS_{env}')}:{getattr(s, f'POSTGRES_PORT_DS_{env}')}"
        f"/{getattr(s, f'POSTGRES_DATABASE_DS_{env}')}"
    )


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema="snapchat",
        include_schemas=True,
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(get_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS snapchat"))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema="snapchat",
            include_schemas=True,
            include_name=include_name,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
