import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

RLS_TABLES = [
    "parking_sessions",
    "parking_cards",
    "shifts",
    "pricing_rules",
    "subscribers",
    "subscription_plans",
    "subscriptions",
    "garage_gates",
    "edge_device_gates",
    "garage_configs",
    "audit_logs",
]


@pytest.mark.asyncio
async def test_rls_table_ownership_and_privileges(pg_session: AsyncSession):
    """Verifies that all 11 RLS tables are owned by pgms_migrator and pgms_app has rolbypassrls = false."""
    session = pg_session

    # 1. Verify pgms_app does NOT bypass RLS
    res_role = await session.execute(
        text("SELECT rolname, rolbypassrls FROM pg_roles WHERE rolname = 'pgms_app';")
    )
    role_row = res_role.fetchone()
    assert role_row is not None
    assert role_row[0] == "pgms_app"
    assert role_row[1] is False, "pgms_app MUST have rolbypassrls = false"

    # 2. Verify all 11 tables are owned by pgms_migrator and have rowsecurity and forcerowsecurity enabled
    res_tables = await session.execute(
        text(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, r.rolname AS owner
            FROM pg_class c
            JOIN pg_roles r ON r.oid = c.relowner
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = ANY(:tables);
            """
        ),
        {"tables": RLS_TABLES},
    )
    rows = {r[0]: {"rls": r[1], "force": r[2], "owner": r[3]} for r in res_tables.fetchall()}

    for tbl in RLS_TABLES:
        assert tbl in rows, f"Table {tbl} not found in public schema"
        assert rows[tbl]["owner"] == "pgms_migrator", f"Table {tbl} must be owned by pgms_migrator"
        assert rows[tbl]["rls"] is True, f"Table {tbl} must have rowsecurity enabled"
        assert rows[tbl]["force"] is True, f"Table {tbl} must have forcerowsecurity enabled"


@pytest.mark.asyncio
async def test_rls_policies_silence_without_context(pg_session: AsyncSession, pg_migrator_session: AsyncSession):
    """Verifies that without setting app.current_garage_id, 0 rows are returned across all 11 RLS tables."""
    migrator = pg_migrator_session
    app_session = pg_session

    try:
        # Seed data using migrator (bypass RLS)
        await migrator.execute(text("INSERT INTO owners (id, name, contact_phone, contact_email) VALUES (991, 'RLS Owner', '123', 'rls@test.com') ON CONFLICT DO NOTHING;"))
        await migrator.execute(text("INSERT INTO garages (id, owner_id, name, code, capacity) VALUES (991, 991, 'RLS Garage 1', 'GAR-RLS-1', 100) ON CONFLICT DO NOTHING;"))
        await migrator.execute(text("INSERT INTO parking_cards (id, garage_id, card_code, status) VALUES (991, 991, 'CARD-RLS-1', 'AVAILABLE') ON CONFLICT DO NOTHING;"))
        await migrator.commit()

        # Query with pgms_app without setting context
        await app_session.execute(text("RESET ALL;"))
        res = await app_session.execute(text("SELECT count(*) FROM parking_cards;"))
        count = res.scalar()
        assert count == 0, f"Expected 0 cards without RLS context, got {count}"
    finally:
        await migrator.execute(text("DELETE FROM parking_cards WHERE id = 991;"))
        await migrator.execute(text("DELETE FROM garages WHERE id = 991;"))
        await migrator.execute(text("DELETE FROM owners WHERE id = 991;"))
        await migrator.commit()


@pytest.mark.asyncio
async def test_rls_positive_isolation_and_with_check(pg_session: AsyncSession, pg_migrator_session: AsyncSession):
    """Verifies that setting app.current_garage_id isolates tenant rows and WITH CHECK blocks cross-insert."""
    migrator = pg_migrator_session
    app_session = pg_session

    try:
        # Seed 2 garages and cards
        await migrator.execute(text("INSERT INTO owners (id, name, contact_phone, contact_email) VALUES (992, 'RLS Owner 2', '124', 'rls2@test.com') ON CONFLICT DO NOTHING;"))
        await migrator.execute(text("INSERT INTO garages (id, owner_id, name, code, capacity) VALUES (992, 992, 'RLS G1', 'GAR-RLS-G1', 100) ON CONFLICT DO NOTHING;"))
        await migrator.execute(text("INSERT INTO garages (id, owner_id, name, code, capacity) VALUES (993, 992, 'RLS G2', 'GAR-RLS-G2', 100) ON CONFLICT DO NOTHING;"))
        await migrator.execute(text("INSERT INTO parking_cards (id, garage_id, card_code, status) VALUES (992, 992, 'CARD-G1', 'AVAILABLE') ON CONFLICT DO NOTHING;"))
        await migrator.execute(text("INSERT INTO parking_cards (id, garage_id, card_code, status) VALUES (993, 993, 'CARD-G2', 'AVAILABLE') ON CONFLICT DO NOTHING;"))
        await migrator.commit()

        # 1. Query under Garage 992 context -> Sees ONLY CARD-G1
        await app_session.execute(text("SET LOCAL app.current_garage_id = '992';"))
        res_g1 = await app_session.execute(text("SELECT card_code FROM parking_cards;"))
        codes_g1 = [r[0] for r in res_g1.fetchall()]
        assert "CARD-G1" in codes_g1
        assert "CARD-G2" not in codes_g1

        # 2. Query under Garage 993 context -> Sees ONLY CARD-G2
        await app_session.execute(text("SET LOCAL app.current_garage_id = '993';"))
        res_g2 = await app_session.execute(text("SELECT card_code FROM parking_cards;"))
        codes_g2 = [r[0] for r in res_g2.fetchall()]
        assert "CARD-G2" in codes_g2
        assert "CARD-G1" not in codes_g2

        # 3. WITH CHECK Assertion: Attempt to insert card with garage_id=993 while context is 992
        await app_session.execute(text("SET LOCAL app.current_garage_id = '992';"))
        with pytest.raises((DBAPIError, ProgrammingError)) as exc_info:
            await app_session.execute(
                text("INSERT INTO parking_cards (id, garage_id, card_code, status) VALUES (994, 993, 'ROGUE-CARD', 'AVAILABLE');")
            )
        assert "row-level security" in str(exc_info.value).lower() or "policy" in str(exc_info.value).lower()
    finally:
        await migrator.execute(text("DELETE FROM parking_cards WHERE id IN (992, 993, 994);"))
        await migrator.execute(text("DELETE FROM garages WHERE id IN (992, 993);"))
        await migrator.execute(text("DELETE FROM owners WHERE id = 992;"))
        await migrator.commit()
