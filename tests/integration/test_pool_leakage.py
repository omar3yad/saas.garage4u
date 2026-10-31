import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from tests.conftest import PG_APP_URL


@pytest.mark.asyncio
async def test_connection_pool_context_isolation_single_connection():
    """Verifies that RLS and tenant session parameters do not leak across pooled connections."""
    # Force single connection pool
    single_conn_engine = create_async_engine(
        PG_APP_URL,
        pool_size=1,
        max_overflow=0,
    )

    try:
        # Request 1: Set tenant context to garage 1
        async with single_conn_engine.connect() as conn1:
            await conn1.execute(text("SET LOCAL app.current_garage_id = '101';"))
            val1 = (await conn1.execute(text("SELECT current_setting('app.current_garage_id', true);"))).scalar()
            assert val1 == "101"
            # Transaction commits or closes, connection returns to pool

        # Request 2: Re-acquire the same physical connection from pool
        async with single_conn_engine.connect() as conn2:
            val2 = (await conn2.execute(text("SELECT current_setting('app.current_garage_id', true);"))).scalar()
            # Must be empty or None, NEVER 101!
            assert val2 in ("", None), f"Context leaked across pool: expected None/'', got {val2}"

        # Request 3: Set is_system_context = 'true'
        async with single_conn_engine.connect() as conn3:
            await conn3.execute(text("SET LOCAL app.is_system_context = 'true';"))
            sys_val = (await conn3.execute(text("SELECT current_setting('app.is_system_context', true);"))).scalar()
            assert sys_val == "true"

        # Request 4: Ensure system context is wiped on subsequent request
        async with single_conn_engine.connect() as conn4:
            sys_after = (await conn4.execute(text("SELECT current_setting('app.is_system_context', true);"))).scalar()
            assert sys_after in ("", None, "false"), f"System context leaked across pool: got {sys_after}"

    finally:
        await single_conn_engine.dispose()
