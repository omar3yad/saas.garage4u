import asyncio
from logging.config import fileConfig
import os

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool, StaticPool
from alembic import context

# Import settings and configure database URL
from config import get_settings
settings = get_settings()

config = context.config

def get_url() -> str:
    url = settings.MIGRATION_DATABASE_URL or settings.DATABASE_URL
    if url.startswith("sqlite"):
        prefix = "sqlite+aiosqlite:///"
        if url.startswith(prefix):
            rel_path = url[len(prefix):]
            if not os.path.isabs(rel_path.replace("./", "").replace("/", "\\")):
                clean_rel = rel_path.lstrip("./").lstrip(".\\")
                abs_db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), clean_rel).replace("\\", "/")
                url = f"{prefix}{abs_db_path}"
    return url

config.set_main_option("sqlalchemy.url", get_url())

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import models to register metadata
from database import Base
import models
target_metadata = Base.metadata

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode using migration role with BYPASSRLS."""
    migration_url = get_url()
    if migration_url.startswith("sqlite"):
        engine = create_async_engine(
            migration_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        engine = create_async_engine(
            migration_url,
            poolclass=NullPool,
        )

    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await engine.dispose()

if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
