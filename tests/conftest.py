import models
import pytest
import httpx
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from config import Settings, get_settings
from database import Base, get_db
from main import app
from repositories.user_repo import UserRepository
from services.audit_service import AuditService
from services.auth_service import AuthService

import os

@pytest.fixture(scope="session")
def settings_override() -> Settings:
    return Settings(
        DATABASE_URL="sqlite+aiosqlite:///test_temp.db",
        SECRET_KEY="a" * 32,
        ENVIRONMENT="development",
        DEBUG=True,
        JWT_EXPIRE_HOURS=1,
    )

@pytest.fixture(scope="session")
async def engine(settings_override: Settings):
    # Remove old test_temp.db if it exists
    if os.path.exists("test_temp.db"):
        try:
            os.remove("test_temp.db")
        except Exception:
            pass

    # Create the test async engine
    test_engine = create_async_engine(
        settings_override.DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
    yield test_engine
    # dispose engine on cleanup
    await test_engine.dispose()

    # Clean up test_temp.db file
    if os.path.exists("test_temp.db"):
        try:
            os.remove("test_temp.db")
        except Exception:
            pass

@pytest.fixture(scope="function", autouse=True)
async def db_tables(engine):
    # Recreate tables for every test function to guarantee a clean state
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest.fixture(scope="function")
async def db_session(engine) -> AsyncSession:
    # Simplify db_session: tables are dropped and recreated per-test
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session

@pytest.fixture(scope="function")
def auth_service(settings_override: Settings) -> AuthService:
    return AuthService(settings_override)

@pytest.fixture(scope="function")
def user_repo(db_session: AsyncSession) -> UserRepository:
    return UserRepository(db_session)

@pytest.fixture(scope="function")
def audit_service(db_session: AsyncSession) -> AuditService:
    return AuditService(db_session)

@pytest.fixture(scope="function")
async def async_client(engine, settings_override: Settings):
    async def override_get_db():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    def override_get_settings():
        return settings_override

    # Apply overrides
    import main
    import database
    orig_main_engine = getattr(main, 'engine', None)
    orig_db_engine = getattr(database, 'engine', None)
    main.engine = engine
    database.engine = engine
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = override_get_settings

    # Instantiate HTTPX client using ASGITransport
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test.local") as client:
        yield client

    # Clean up overrides
    app.dependency_overrides.clear()
    main.engine = orig_main_engine
    database.engine = orig_db_engine


# ---------------------------------------------------------------------------
# Task 11.1: Real PostgreSQL Fixtures for RLS & Multi-Tenant Integration Tests
# ---------------------------------------------------------------------------

PG_APP_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://pgms_app:PgAppSecurePass2026x@127.0.0.1:5432/pgms_saas_db",
)

PG_MIGRATOR_URL = os.environ.get(
    "TEST_MIGRATION_DATABASE_URL",
    "postgresql+asyncpg://pgms_migrator:PgMigratorSecurePass2026x@127.0.0.1:5432/pgms_saas_db",
)


@pytest.fixture(scope="session")
def pg_settings() -> Settings:
    return Settings(
        DATABASE_URL=PG_APP_URL,
        MIGRATION_DATABASE_URL=PG_MIGRATOR_URL,
        SECRET_KEY="e" * 32,
        ENVIRONMENT="development",
        DEBUG=True,
        JWT_EXPIRE_HOURS=1,
    )


@pytest.fixture(scope="function")
async def pg_engine():
    """Async engine connected as unprivileged pgms_app user subject to RLS."""
    test_pg_engine = create_async_engine(
        PG_APP_URL,
        pool_size=5,
        max_overflow=10,
    )
    yield test_pg_engine
    await test_pg_engine.dispose()


@pytest.fixture(scope="function")
async def pg_migrator_engine():
    """Async engine connected as privileged pgms_migrator user (BYPASSRLS)."""
    migrator_engine = create_async_engine(
        PG_MIGRATOR_URL,
        pool_size=5,
        max_overflow=10,
    )
    yield migrator_engine
    await migrator_engine.dispose()


@pytest.fixture(scope="function")
async def pg_session(pg_engine) -> AsyncSession:
    """AsyncSession for pgms_app subject to RLS."""
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture(scope="function")
async def pg_migrator_session(pg_migrator_engine) -> AsyncSession:
    """AsyncSession for pgms_migrator for administrative setups and seedings."""
    async with AsyncSession(pg_migrator_engine, expire_on_commit=False) as session:
        yield session

