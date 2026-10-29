"""Phase 5 Revision 3: Seed default entities, configure sequences, and backfill operational tables

Revision ID: e03_phase5_rev3
Revises: e02_phase5_rev2
Create Date: 2026-10-29 14:55:00.000000

ARCHITECTURAL NOTES:
- Seeds default Owner (id=1, Babelooq Parking Co).
- Seeds default Garage (id=1, BABELOOQ, 500 capacity).
- Immediately aligns owners_id_seq and garages_id_seq to prevent future Duplicate Key errors.
- Dynamically creates Babelooq garage gates based on historical sessions and shifts (at least gates 1 & 2).
- Migrates existing camera EDGE_API_TOKEN (if present) into edge_devices and edge_device_gates.
- Seeds default garage_configs for Babelooq garage.
- Backfills garage_id=1 on all historical operational records.
- Backfills parking_sessions.receipt_no and sets garage_counters atomic counter.
"""
import hashlib
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from config import get_settings


# revision identifiers, used by Alembic.
revision: str = 'e03_phase5_rev3'
down_revision: Union[str, None] = 'e02_phase5_rev2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    is_pg = conn.dialect.name == "postgresql"
    settings = get_settings()

    # 1. Seed Default Owner (id=1)
    conn.execute(sa.text("""
        INSERT INTO owners (id, name, company_name, contact_phone, contact_email, subscription_plan, billing_status, max_garages_allowed)
        VALUES (1, 'شركة باب اللوق', 'Babelooq Parking Co', '01000000000', 'owner@babelooq.com', 'STANDARD', 'ACTIVE', 5)
        ON CONFLICT (id) DO NOTHING;
    """))

    # 2. Seed Default Garage (id=1)
    conn.execute(sa.text("""
        INSERT INTO garages (id, owner_id, code, name, capacity, timezone, currency, status, can_use_anpr, can_use_subscriptions, max_gates)
        VALUES (1, 1, 'BABELOOQ', 'جراج باب اللوق', 500, 'Africa/Cairo', 'EGP', 'ACTIVE', FALSE, TRUE, 5)
        ON CONFLICT (id) DO NOTHING;
    """))

    # 3. Synchronize PostgreSQL Sequences for manually inserted IDs
    if is_pg:
        conn.execute(sa.text("SELECT setval('owners_id_seq', (SELECT COALESCE(MAX(id), 1) FROM owners));"))
        conn.execute(sa.text("SELECT setval('garages_id_seq', (SELECT COALESCE(MAX(id), 1) FROM garages));"))

    # 4. Dynamically Create Babelooq Gates from historical sessions/shifts (at least gates 1 & 2)
    conn.execute(sa.text("""
        INSERT INTO garage_gates (garage_id, gate_number, name, gate_type, default_terminal_profile, paper_width_mm)
        SELECT 
            1 AS garage_id,
            g.gate_number,
            'بوابة ' || g.gate_number AS name,
            'MANUAL_BIDIRECTIONAL' AS gate_type,
            'DESKTOP_GATE_KIOSK' AS default_terminal_profile,
            80 AS paper_width_mm
        FROM (
            SELECT 1 AS gate_number UNION SELECT 2
            UNION
            SELECT DISTINCT gate_number FROM parking_sessions WHERE gate_number IS NOT NULL
            UNION
            SELECT DISTINCT gate_number FROM shifts WHERE gate_number IS NOT NULL
        ) g
        WHERE g.gate_number BETWEEN 1 AND 10
        ON CONFLICT (garage_id, gate_number) DO NOTHING;
    """))

    # 5. Migrate camera edge token if configured in environment
    edge_token = settings.EDGE_API_TOKEN
    if edge_token and len(edge_token.strip()) >= 32:
        token_hash = hashlib.sha256(edge_token.strip().encode("utf-8")).hexdigest()
        conn.execute(sa.text("""
            INSERT INTO edge_devices (garage_id, device_name, token_hash)
            VALUES (1, 'Gate 1-2 Default Workstation', :token_hash)
            ON CONFLICT (token_hash) DO NOTHING;
        """), {"token_hash": token_hash})

        device_id = conn.execute(sa.text("""
            SELECT id FROM edge_devices WHERE token_hash = :token_hash;
        """), {"token_hash": token_hash}).scalar()

        if device_id:
            conn.execute(sa.text("""
                INSERT INTO edge_device_gates (device_id, garage_id, gate_number, direction, is_active)
                VALUES 
                    (:device_id, 1, 1, 'BIDIRECTIONAL', true),
                    (:device_id, 1, 2, 'BIDIRECTIONAL', true)
                ON CONFLICT (device_id, garage_id, gate_number, direction) DO NOTHING;
            """), {"device_id": device_id})

    # 6. Seed Default Garage Configuration
    conn.execute(sa.text("""
        INSERT INTO garage_configs (
            garage_id, ticket_system, anpr_enabled, exit_photo_enabled,
            subscriptions_enabled, require_shift_cash_reconciliation,
            min_session_duration_seconds, version
        ) VALUES (
            1, 'REUSABLE_CARDS', FALSE, FALSE, TRUE, TRUE, 60, 1
        )
        ON CONFLICT (garage_id) DO NOTHING;
    """))

    # 7. Backfill garage_id = 1 on existing operational tables
    conn.execute(sa.text("UPDATE parking_cards SET garage_id = 1 WHERE garage_id IS NULL;"))
    conn.execute(sa.text("UPDATE shifts SET garage_id = 1 WHERE garage_id IS NULL;"))
    conn.execute(sa.text("UPDATE pricing_rules SET garage_id = 1 WHERE garage_id IS NULL;"))
    conn.execute(sa.text("UPDATE parking_sessions SET garage_id = 1 WHERE garage_id IS NULL;"))
    conn.execute(sa.text("UPDATE subscribers SET garage_id = 1 WHERE garage_id IS NULL;"))
    conn.execute(sa.text("UPDATE subscription_plans SET garage_id = 1 WHERE garage_id IS NULL;"))
    conn.execute(sa.text("UPDATE subscriptions SET garage_id = 1 WHERE garage_id IS NULL;"))
    conn.execute(sa.text("UPDATE audit_logs SET garage_id = 1 WHERE garage_id IS NULL;"))

    # 8. Populate sequential receipt numbers for historical sessions & set atomic counter
    conn.execute(sa.text("UPDATE parking_sessions SET receipt_no = id WHERE receipt_no IS NULL;"))
    conn.execute(sa.text("""
        INSERT INTO garage_counters (garage_id, last_receipt_no)
        VALUES (1, (SELECT COALESCE(MAX(id), 0) FROM parking_sessions))
        ON CONFLICT (garage_id) DO UPDATE SET last_receipt_no = EXCLUDED.last_receipt_no;
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM garage_counters WHERE garage_id = 1;"))
    conn.execute(sa.text("DELETE FROM garage_configs WHERE garage_id = 1;"))
    conn.execute(sa.text("DELETE FROM edge_device_gates WHERE garage_id = 1;"))
    conn.execute(sa.text("DELETE FROM edge_devices WHERE garage_id = 1;"))
    conn.execute(sa.text("DELETE FROM garage_gates WHERE garage_id = 1;"))
    conn.execute(sa.text("UPDATE audit_logs SET garage_id = NULL WHERE garage_id = 1;"))
    conn.execute(sa.text("UPDATE subscriptions SET garage_id = NULL WHERE garage_id = 1;"))
    conn.execute(sa.text("UPDATE subscription_plans SET garage_id = NULL WHERE garage_id = 1;"))
    conn.execute(sa.text("UPDATE subscribers SET garage_id = NULL WHERE garage_id = 1;"))
    conn.execute(sa.text("UPDATE parking_sessions SET garage_id = NULL, receipt_no = NULL WHERE garage_id = 1;"))
    conn.execute(sa.text("UPDATE pricing_rules SET garage_id = NULL WHERE garage_id = 1;"))
    conn.execute(sa.text("UPDATE shifts SET garage_id = NULL WHERE garage_id = 1;"))
    conn.execute(sa.text("UPDATE parking_cards SET garage_id = NULL WHERE garage_id = 1;"))
    conn.execute(sa.text("DELETE FROM garages WHERE id = 1;"))
    conn.execute(sa.text("DELETE FROM owners WHERE id = 1;"))
