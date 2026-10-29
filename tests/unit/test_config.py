import pytest
from pydantic import ValidationError
from config import Settings

def test_config_development_defaults_migration_database_url():
    """In development, if MIGRATION_DATABASE_URL is not set, it defaults to DATABASE_URL."""
    settings = Settings(
        _env_file=None,
        DATABASE_URL="sqlite+aiosqlite:///dev.db",
        SECRET_KEY="a" * 32,
        ENVIRONMENT="development",
        DEBUG=True,
    )
    assert settings.MIGRATION_DATABASE_URL == "sqlite+aiosqlite:///dev.db"

def test_config_development_preserves_explicit_migration_database_url():
    """In development, if MIGRATION_DATABASE_URL is explicitly set, it is preserved."""
    settings = Settings(
        _env_file=None,
        DATABASE_URL="sqlite+aiosqlite:///dev.db",
        MIGRATION_DATABASE_URL="sqlite+aiosqlite:///migrator.db",
        SECRET_KEY="a" * 32,
        ENVIRONMENT="development",
        DEBUG=True,
    )
    assert settings.MIGRATION_DATABASE_URL == "sqlite+aiosqlite:///migrator.db"

def test_config_production_requires_migration_database_url():
    """In production, missing MIGRATION_DATABASE_URL raises ValueError."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            DATABASE_URL="postgresql+asyncpg://pgms_app:pass@127.0.0.1:5432/pgms_saas_db",
            MIGRATION_DATABASE_URL=None,
            SECRET_KEY="a" * 32,
            ENVIRONMENT="production",
            DEBUG=False,
        )
    assert "MIGRATION_DATABASE_URL must be provided in production" in str(exc_info.value)

def test_config_production_requires_pgms_migrator_role():
    """In production, MIGRATION_DATABASE_URL must use the pgms_migrator account."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            DATABASE_URL="postgresql+asyncpg://pgms_app:pass@127.0.0.1:5432/pgms_saas_db",
            MIGRATION_DATABASE_URL="postgresql+asyncpg://other_user:pass@127.0.0.1:5432/pgms_saas_db",
            SECRET_KEY="a" * 32,
            ENVIRONMENT="production",
            DEBUG=False,
        )
    assert "MIGRATION_DATABASE_URL must use the pgms_migrator account in production" in str(exc_info.value)

def test_config_production_valid_migration_database_url():
    """In production, valid MIGRATION_DATABASE_URL with pgms_migrator is accepted."""
    migrator_url = "postgresql+asyncpg://pgms_migrator:secret@127.0.0.1:5432/pgms_saas_db"
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://pgms_app:pass@127.0.0.1:5432/pgms_saas_db",
        MIGRATION_DATABASE_URL=migrator_url,
        SECRET_KEY="a" * 32,
        ENVIRONMENT="production",
        DEBUG=False,
    )
    assert settings.MIGRATION_DATABASE_URL == migrator_url
