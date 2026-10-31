import os
from typing import AsyncGenerator
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool
from config import get_settings

settings = get_settings()

db_url = settings.DATABASE_URL
if db_url.startswith("sqlite"):
    prefix = "sqlite+aiosqlite:///"
    if db_url.startswith(prefix):
        rel_path = db_url[len(prefix):]
        if not os.path.isabs(rel_path.replace("./", "").replace("/", "\\")):
            clean_rel = rel_path.lstrip("./").lstrip(".\\")
            abs_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), clean_rel).replace("\\", "/")
            db_url = f"{prefix}{abs_db_path}"

    engine = create_async_engine(
        db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=DELETE")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()
else:
    engine = create_async_engine(
        db_url,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
    )

from sqlalchemy import text
from sqlalchemy.orm import Session

@event.listens_for(Session, "after_begin")
def receive_after_begin(session, transaction, connection):
    """Synchronous session event listener to inject PostgreSQL transaction-local RLS settings."""
    if connection.dialect.name != "postgresql":
        return
    info = session.info
    garage_id_str = str(info.get("garage_id")) if info.get("garage_id") is not None else ""
    owner_id_str = str(info.get("owner_id")) if info.get("owner_id") is not None else ""
    is_super_admin_str = "true" if info.get("is_super_admin") else "false"
    is_system_str = "true" if (info.get("is_system") or info.get("is_system_context")) else "false"

    connection.execute(
        text("""
            SELECT 
                set_config('app.current_garage_id', :gid, true),
                set_config('app.current_owner_id', :oid, true),
                set_config('app.is_super_admin', :isa, true),
                set_config('app.is_system_context', :sys, true);
        """),
        {"gid": garage_id_str, "oid": owner_id_str, "isa": is_super_admin_str, "sys": is_system_str}
    )

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

class Base(DeclarativeBase):
    pass

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    session = AsyncSessionLocal()
    try:
        yield session
    finally:
        await session.close()

__all__ = ["engine", "AsyncSessionLocal", "Base", "get_db"]
