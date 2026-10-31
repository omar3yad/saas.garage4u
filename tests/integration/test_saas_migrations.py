import os
import subprocess
import pytest
import asyncpg
from datetime import datetime

MIG_TEST_DB = "pgms_saas_mig_test"
ADMIN_CONN_URL = "postgresql://pgms_migrator:PgMigratorSecurePass2026x@127.0.0.1:5432/postgres"
TEST_DB_URL = f"postgresql://pgms_migrator:PgMigratorSecurePass2026x@127.0.0.1:5432/{MIG_TEST_DB}"
ASYNC_TEST_DB_URL = f"postgresql+asyncpg://pgms_migrator:PgMigratorSecurePass2026x@127.0.0.1:5432/{MIG_TEST_DB}"


def run_alembic(command: list[str]):
    env = os.environ.copy()
    env["MIGRATION_DATABASE_URL"] = ASYNC_TEST_DB_URL
    env["DATABASE_URL"] = ASYNC_TEST_DB_URL
    result = subprocess.run(
        [".venv/bin/alembic"] + command,
        cwd="/srv/pgms-saas/app",
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Alembic {' '.join(command)} failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    return result.stdout


@pytest.fixture(scope="module")
def setup_migration_test_db():
    import asyncio

    async def _setup():
        conn = await asyncpg.connect(ADMIN_CONN_URL)
        try:
            # Terminate connections to test db if any
            await conn.execute(f"""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = '{MIG_TEST_DB}' AND pid <> pg_backend_pid();
            """)
            await conn.execute(f"DROP DATABASE IF EXISTS {MIG_TEST_DB};")
            await conn.execute(f"CREATE DATABASE {MIG_TEST_DB} OWNER pgms_migrator;")
        finally:
            await conn.close()

    async def _teardown():
        conn = await asyncpg.connect(ADMIN_CONN_URL)
        try:
            await conn.execute(f"""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = '{MIG_TEST_DB}' AND pid <> pg_backend_pid();
            """)
            await conn.execute(f"DROP DATABASE IF EXISTS {MIG_TEST_DB};")
        finally:
            await conn.close()

    asyncio.run(_setup())
    yield
    asyncio.run(_teardown())


@pytest.mark.asyncio
async def test_saas_migrations_historical_data_and_rollback(setup_migration_test_db):
    """Task 11.8: Verifies Phase 5 migration pipeline, gap handling, counter initialization, and rollback."""
    # 1. Migrate up to c9f5e1a82d06 (Pre-Phase 5 Head)
    run_alembic(["upgrade", "c9f5e1a82d06"])

    conn = await asyncpg.connect(TEST_DB_URL)
    try:
        # 2. Seed pre-Phase 5 historical data with non-contiguous IDs & multiple historical gates
        # Seed users
        await conn.execute("""
            INSERT INTO users (id, username, full_name, role, gate_number, hashed_password, is_active)
            VALUES (1, 'legacy_admin', 'Legacy Admin', 'ADMIN', NULL, 'fakehash', true),
                   (2, 'legacy_op', 'Legacy Operator', 'OPERATOR', 1, 'fakehash', true);
        """)
        # Seed pricing rule
        await conn.execute("""
            INSERT INTO pricing_rules (id, label, rate_per_hour, grace_period_mins, lost_card_penalty, is_active, created_by, effective_from)
            VALUES (1, 'Legacy Standard', 10, 15, 50, true, 1, NOW());
        """)
        # Seed non-contiguous shifts across historical gates (1, 2, 3, 5)
        await conn.execute("""
            INSERT INTO shifts (id, operator_id, gate_number, started_at, opening_cash_egp)
            VALUES (1, 2, 1, NOW() - INTERVAL '10 hours', 100),
                   (3, 2, 2, NOW() - INTERVAL '8 hours', 100),
                   (7, 2, 3, NOW() - INTERVAL '6 hours', 100),
                   (10, 2, 5, NOW() - INTERVAL '4 hours', 100);
        """)
        # Seed non-contiguous cards
        await conn.execute("""
            INSERT INTO parking_cards (id, card_code, status)
            VALUES (1, 'CRD-HIST-01', 'AVAILABLE'),
                   (3, 'CRD-HIST-03', 'AVAILABLE'),
                   (7, 'CRD-HIST-07', 'AVAILABLE'),
                   (10, 'CRD-HIST-10', 'AVAILABLE');
        """)
        # Seed non-contiguous sessions across historical gates (1, 2, 3, 5)
        await conn.execute("""
            INSERT INTO parking_sessions (id, card_id, card_code, shift_id, operator_id, gate_number, entry_time, status)
            VALUES (1, 1, 'CRD-HIST-01', 1, 2, 1, NOW() - INTERVAL '9 hours', 'ACTIVE'),
                   (3, 3, 'CRD-HIST-03', 3, 2, 2, NOW() - INTERVAL '7 hours', 'ACTIVE'),
                   (7, 7, 'CRD-HIST-07', 7, 2, 3, NOW() - INTERVAL '5 hours', 'ACTIVE'),
                   (10, 10, 'CRD-HIST-10', 10, 2, 5, NOW() - INTERVAL '3 hours', 'ACTIVE');
        """)
    finally:
        await conn.close()

    # 3. Execute Phase 5 Revisions to Head
    # Run revision 1 first to commit new enum values (PostgreSQL requirement)
    run_alembic(["upgrade", "e01_phase5_rev1"])
    run_alembic(["upgrade", "head"])

    conn = await asyncpg.connect(TEST_DB_URL)
    try:
        # A. Verify all historic gates (1, 2, 3, 5) created in garage_gates for Bab El-Louq (garage_id=1)
        gates = await conn.fetch("SELECT gate_number FROM garage_gates WHERE garage_id = 1 ORDER BY gate_number;")
        gate_nums = [g["gate_number"] for g in gates]
        for expected_gate in (1, 2, 3, 5):
            assert expected_gate in gate_nums, f"Gate {expected_gate} was not backfilled into garage_gates"

        # B. Verify receipt_no = id on historic sessions
        sessions = await conn.fetch("SELECT id, receipt_no, garage_id FROM parking_sessions WHERE garage_id = 1 ORDER BY id;")
        assert len(sessions) == 4
        for s in sessions:
            assert s["receipt_no"] == s["id"], f"Session {s['id']} has receipt_no {s['receipt_no']} != id"

        # C. Verify garage_counters initialized to MAX(id) = 10
        counter = await conn.fetchrow("SELECT last_receipt_no FROM garage_counters WHERE garage_id = 1;")
        assert counter is not None
        assert counter["last_receipt_no"] == 10, f"Expected counter=10, got {counter['last_receipt_no']}"

        # D. Verify atomic generation of next receipt_no without collision
        # Increment counter atomically
        next_receipt = await conn.fetchval("""
            UPDATE garage_counters
            SET last_receipt_no = last_receipt_no + 1
            WHERE garage_id = 1
            RETURNING last_receipt_no;
        """)
        assert next_receipt == 11

        # Sync sequence for manual historical ID insertions
        await conn.execute("SELECT setval('parking_sessions_id_seq', (SELECT MAX(id) FROM parking_sessions));")

        # Insert new session with receipt_no = 11
        await conn.execute("""
            INSERT INTO parking_sessions (garage_id, receipt_no, ticket_code, shift_id, operator_id, gate_number, entry_time, status)
            VALUES (1, $1, 'TKT-11', 1, 2, 1, NOW(), 'ACTIVE');
        """, next_receipt)

        # Confirm UNIQUE(garage_id, receipt_no) allows 11
        val_11 = await conn.fetchval("SELECT receipt_no FROM parking_sessions WHERE garage_id = 1 AND receipt_no = 11;")
        assert val_11 == 11

    finally:
        await conn.close()

    # 4. Verify Downgrade back to c9f5e1a82d06
    run_alembic(["downgrade", "c9f5e1a82d06"])

    # 5. Verify Clean Re-Upgrade to Head
    run_alembic(["upgrade", "e01_phase5_rev1"])
    run_alembic(["upgrade", "head"])
