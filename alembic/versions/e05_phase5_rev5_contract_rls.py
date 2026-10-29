"""Phase 5 Revision 5: Contract nullable columns to NOT NULL, apply composite keys, audit_logs constraints, and enable RLS on 11 operational tables

Revision ID: e05_phase5_rev5
Revises: e04_phase5_rev4
Create Date: 2026-10-29 15:10:00.000000

ARCHITECTURAL NOTES:
- Contract phase of Expand/Contract migration pattern.
- Converts garage_id to NOT NULL across the 7 operational tables and receipt_no in parking_sessions.
- Drops obsolete legacy unique constraints and establishes scoped composite unique constraints.
- Establishes UNIQUE (id, garage_id) and composite foreign keys to guarantee DB-level tenant isolation.
- Enforces strict audit_logs context constraint (ck_audit_garage_context).
- Enables and FORCE-enables PostgreSQL Row-Level Security (RLS) on the 11 designated tables
  with inline safe casting (NULLIF(..., '')::INTEGER) and explicit named policies.
- Keeps the 5 non-RLS tables (owners, garages, users, garage_counters, edge_devices)
  isolated at the application layer via Scoped Repositories.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e05_phase5_rev5'
down_revision: Union[str, None] = 'e04_phase5_rev4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RLS_TABLES_MAP = {
    "parking_sessions": "rls_policy_sessions",
    "parking_cards": "rls_policy_cards",
    "shifts": "rls_policy_shifts",
    "pricing_rules": "rls_policy_rates",
    "subscribers": "rls_policy_subscribers",
    "subscription_plans": "rls_policy_plans",
    "subscriptions": "rls_policy_subs",
    "garage_gates": "rls_policy_gates",
    "edge_device_gates": "rls_policy_edge_gates",
    "garage_configs": "rls_policy_configs",
}


def upgrade() -> None:
    conn = op.get_bind()
    is_pg = conn.dialect.name == "postgresql"

    # --------------------------------------------------------------------------
    # 1. Convert columns to NOT NULL (Contract phase)
    # --------------------------------------------------------------------------
    if is_pg:
        conn.execute(sa.text("ALTER TABLE parking_cards ALTER COLUMN garage_id SET NOT NULL;"))
        conn.execute(sa.text("ALTER TABLE shifts ALTER COLUMN garage_id SET NOT NULL;"))
        conn.execute(sa.text("ALTER TABLE pricing_rules ALTER COLUMN garage_id SET NOT NULL;"))
        conn.execute(sa.text("ALTER TABLE parking_sessions ALTER COLUMN garage_id SET NOT NULL;"))
        conn.execute(sa.text("ALTER TABLE parking_sessions ALTER COLUMN receipt_no SET NOT NULL;"))
        conn.execute(sa.text("ALTER TABLE subscribers ALTER COLUMN garage_id SET NOT NULL;"))
        conn.execute(sa.text("ALTER TABLE subscription_plans ALTER COLUMN garage_id SET NOT NULL;"))
        conn.execute(sa.text("ALTER TABLE subscriptions ALTER COLUMN garage_id SET NOT NULL;"))
    else:
        with op.batch_alter_table("parking_cards") as b:
            b.alter_column("garage_id", nullable=False)
        with op.batch_alter_table("shifts") as b:
            b.alter_column("garage_id", nullable=False)
        with op.batch_alter_table("pricing_rules") as b:
            b.alter_column("garage_id", nullable=False)
        with op.batch_alter_table("parking_sessions") as b:
            b.alter_column("garage_id", nullable=False)
            b.alter_column("receipt_no", nullable=False)
        with op.batch_alter_table("subscribers") as b:
            b.alter_column("garage_id", nullable=False)
        with op.batch_alter_table("subscription_plans") as b:
            b.alter_column("garage_id", nullable=False)
        with op.batch_alter_table("subscriptions") as b:
            b.alter_column("garage_id", nullable=False)

    # --------------------------------------------------------------------------
    # 2. Drop legacy global unique constraints and apply scoped composite constraints
    # --------------------------------------------------------------------------
    if is_pg:
        # 2.1 users
        conn.execute(sa.text("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_username_key;"))
        conn.execute(sa.text("DROP INDEX IF EXISTS ix_users_username;"))
        conn.execute(sa.text("ALTER TABLE users ADD CONSTRAINT uq_users_id_garage UNIQUE (id, garage_id);"))
        conn.execute(sa.text("ALTER TABLE users ADD CONSTRAINT uq_users_garage_username UNIQUE (garage_id, username);"))
        conn.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_global_users_username ON users(username) WHERE garage_id IS NULL;"))
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_users_garage_lookup ON users(garage_id, role);"))

        # 2.2 parking_cards
        conn.execute(sa.text("ALTER TABLE parking_cards DROP CONSTRAINT IF EXISTS parking_cards_card_code_key;"))
        conn.execute(sa.text("DROP INDEX IF EXISTS ix_parking_cards_card_code;"))
        conn.execute(sa.text("ALTER TABLE parking_cards ADD CONSTRAINT uq_parking_cards_id_garage UNIQUE (id, garage_id);"))
        conn.execute(sa.text("ALTER TABLE parking_cards ADD CONSTRAINT uq_garage_card_code UNIQUE (garage_id, card_code);"))
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_parking_cards_garage_status ON parking_cards(garage_id, status);"))

        # 2.3 shifts
        conn.execute(sa.text("ALTER TABLE shifts ADD CONSTRAINT uq_shifts_id_garage UNIQUE (id, garage_id);"))
        conn.execute(sa.text("""
            ALTER TABLE shifts ADD CONSTRAINT fk_shifts_operator_garage 
            FOREIGN KEY (operator_id, garage_id) REFERENCES users(id, garage_id) ON DELETE RESTRICT;
        """))
        conn.execute(sa.text("""
            ALTER TABLE shifts ADD CONSTRAINT fk_shifts_gate_garage 
            FOREIGN KEY (garage_id, gate_number) REFERENCES garage_gates(garage_id, gate_number) ON DELETE RESTRICT;
        """))
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_shifts_garage_lookup ON shifts(garage_id, started_at);"))
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_shifts_garage_active ON shifts(garage_id, gate_number) WHERE ended_at IS NULL;"))

        # 2.4 pricing_rules
        conn.execute(sa.text("ALTER TABLE pricing_rules DROP CONSTRAINT IF EXISTS pricing_rules_label_key;"))
        conn.execute(sa.text("ALTER TABLE pricing_rules ADD CONSTRAINT uq_pricing_rules_id_garage UNIQUE (id, garage_id);"))
        conn.execute(sa.text("ALTER TABLE pricing_rules ADD CONSTRAINT uq_garage_pricing_label UNIQUE (garage_id, label);"))
        conn.execute(sa.text("""
            ALTER TABLE pricing_rules ADD CONSTRAINT fk_pricing_created_by 
            FOREIGN KEY (created_by, garage_id) REFERENCES users(id, garage_id) ON DELETE RESTRICT;
        """))
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pricing_rules_garage ON pricing_rules(garage_id);"))
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pricing_rules_active ON pricing_rules(garage_id) WHERE is_active = TRUE;"))

        # 2.5 subscribers
        conn.execute(sa.text("ALTER TABLE subscribers DROP CONSTRAINT IF EXISTS subscribers_plate_number_key;"))
        conn.execute(sa.text("DROP INDEX IF EXISTS ix_subscribers_card_code;"))
        conn.execute(sa.text("ALTER TABLE subscribers ADD CONSTRAINT uq_subscribers_id_garage UNIQUE (id, garage_id);"))
        conn.execute(sa.text("ALTER TABLE subscribers ADD CONSTRAINT uq_garage_subscriber_plate UNIQUE (garage_id, plate_number);"))
        conn.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_garage_subscriber_card ON subscribers(garage_id, card_code) WHERE card_code IS NOT NULL;"))
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_subscribers_garage ON subscribers(garage_id);"))

        # 2.6 subscription_plans
        conn.execute(sa.text("ALTER TABLE subscription_plans DROP CONSTRAINT IF EXISTS subscription_plans_label_key;"))
        conn.execute(sa.text("DROP INDEX IF EXISTS ix_subscription_plans_label;"))
        conn.execute(sa.text("ALTER TABLE subscription_plans ADD CONSTRAINT uq_subscription_plans_id_garage UNIQUE (id, garage_id);"))
        conn.execute(sa.text("ALTER TABLE subscription_plans ADD CONSTRAINT uq_garage_plan_label UNIQUE (garage_id, label);"))
        conn.execute(sa.text("""
            ALTER TABLE subscription_plans ADD CONSTRAINT fk_plans_created_by 
            FOREIGN KEY (created_by, garage_id) REFERENCES users(id, garage_id) ON DELETE RESTRICT;
        """))
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_subscription_plans_garage ON subscription_plans(garage_id);"))

        # 2.7 subscriptions
        conn.execute(sa.text("ALTER TABLE subscriptions ADD CONSTRAINT uq_subscriptions_id_garage UNIQUE (id, garage_id);"))
        conn.execute(sa.text("""
            ALTER TABLE subscriptions ADD CONSTRAINT fk_subscriptions_subscriber_garage 
            FOREIGN KEY (subscriber_id, garage_id) REFERENCES subscribers(id, garage_id) ON DELETE RESTRICT;
        """))
        conn.execute(sa.text("""
            ALTER TABLE subscriptions ADD CONSTRAINT fk_subscriptions_plan_garage 
            FOREIGN KEY (plan_id, garage_id) REFERENCES subscription_plans(id, garage_id) ON DELETE RESTRICT;
        """))
        conn.execute(sa.text("""
            ALTER TABLE subscriptions ADD CONSTRAINT fk_subscriptions_card_garage 
            FOREIGN KEY (card_id, garage_id) REFERENCES parking_cards(id, garage_id) ON DELETE RESTRICT;
        """))
        conn.execute(sa.text("""
            ALTER TABLE subscriptions ADD CONSTRAINT fk_subscriptions_collected_by 
            FOREIGN KEY (collected_by, garage_id) REFERENCES users(id, garage_id) ON DELETE RESTRICT;
        """))
        conn.execute(sa.text("""
            ALTER TABLE subscriptions ADD CONSTRAINT fk_subscriptions_prev_sub 
            FOREIGN KEY (previous_subscription_id, garage_id) REFERENCES subscriptions(id, garage_id) ON DELETE RESTRICT;
        """))
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_subscriptions_garage_status ON subscriptions(garage_id, status);"))

        # 2.8 parking_sessions
        conn.execute(sa.text("ALTER TABLE parking_sessions ADD CONSTRAINT uq_garage_ticket_code UNIQUE (garage_id, ticket_code);"))
        conn.execute(sa.text("ALTER TABLE parking_sessions ADD CONSTRAINT uq_garage_receipt_no UNIQUE (garage_id, receipt_no);"))
        conn.execute(sa.text("""
            ALTER TABLE parking_sessions ADD CONSTRAINT ck_session_card_or_ticket CHECK (
                (card_id IS NOT NULL AND card_code IS NOT NULL) OR (ticket_code IS NOT NULL)
            );
        """))
        conn.execute(sa.text("""
            ALTER TABLE parking_sessions ADD CONSTRAINT fk_sessions_card_garage 
            FOREIGN KEY (card_id, garage_id) REFERENCES parking_cards(id, garage_id) ON DELETE RESTRICT;
        """))
        conn.execute(sa.text("""
            ALTER TABLE parking_sessions ADD CONSTRAINT fk_sessions_entry_shift_garage 
            FOREIGN KEY (shift_id, garage_id) REFERENCES shifts(id, garage_id) ON DELETE RESTRICT;
        """))
        conn.execute(sa.text("""
            ALTER TABLE parking_sessions ADD CONSTRAINT fk_sessions_exit_shift_garage 
            FOREIGN KEY (exit_shift_id, garage_id) REFERENCES shifts(id, garage_id) ON DELETE RESTRICT;
        """))
        conn.execute(sa.text("""
            ALTER TABLE parking_sessions ADD CONSTRAINT fk_sessions_pricing_garage 
            FOREIGN KEY (pricing_rule_id, garage_id) REFERENCES pricing_rules(id, garage_id) ON DELETE RESTRICT;
        """))
        conn.execute(sa.text("""
            ALTER TABLE parking_sessions ADD CONSTRAINT fk_sessions_operator_garage 
            FOREIGN KEY (operator_id, garage_id) REFERENCES users(id, garage_id) ON DELETE RESTRICT;
        """))
        conn.execute(sa.text("""
            ALTER TABLE parking_sessions ADD CONSTRAINT fk_sessions_exit_operator_garage 
            FOREIGN KEY (exit_operator_id, garage_id) REFERENCES users(id, garage_id) ON DELETE RESTRICT;
        """))
        conn.execute(sa.text("""
            ALTER TABLE parking_sessions ADD CONSTRAINT fk_sessions_admin_override_garage 
            FOREIGN KEY (admin_override_by, garage_id) REFERENCES users(id, garage_id) ON DELETE RESTRICT;
        """))
        conn.execute(sa.text("""
            ALTER TABLE parking_sessions ADD CONSTRAINT fk_sessions_gate_garage 
            FOREIGN KEY (garage_id, gate_number) REFERENCES garage_gates(garage_id, gate_number) ON DELETE RESTRICT;
        """))
        conn.execute(sa.text("""
            ALTER TABLE parking_sessions ADD CONSTRAINT fk_sessions_subscription_garage 
            FOREIGN KEY (subscription_id, garage_id) REFERENCES subscriptions(id, garage_id) ON DELETE RESTRICT;
        """))

        # Atomic partial indexes on parking_sessions
        conn.execute(sa.text("DROP INDEX IF EXISTS idx_active_card_id;"))
        conn.execute(sa.text("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_garage_active_card_id 
            ON parking_sessions (garage_id, card_id) 
            WHERE status = 'ACTIVE' AND card_id IS NOT NULL;
        """))
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_sessions_garage_status ON parking_sessions(garage_id, status);"))
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_sessions_garage_entry_time ON parking_sessions(garage_id, entry_time);"))
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_sessions_garage_plate ON parking_sessions(garage_id, plate_number);"))

        # 2.9 audit_logs constraints & indexes
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_audit_logs_garage_id ON audit_logs(garage_id);"))
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_audit_logs_owner_id ON audit_logs(owner_id);"))
        conn.execute(sa.text("""
            ALTER TABLE audit_logs ADD CONSTRAINT ck_audit_garage_context CHECK (
                (garage_id IS NOT NULL) OR
                (garage_id IS NULL AND (
                    entity_type IN ('platform', 'owner_account', 'system') OR
                    actor_type = 'system' OR
                    action IN ('USER_LOGIN', 'USER_LOGOUT', 'LOGIN_FAILED', 'SUPER_ADMIN_GARAGE_ACCESS')
                ))
            );
        """))

    # --------------------------------------------------------------------------
    # 3. Enable & Force Row-Level Security on the 11 designated operational tables
    # --------------------------------------------------------------------------
    if is_pg:
        # Standard RLS policy template for 10 operational tables
        for table_name, policy_name in RLS_TABLES_MAP.items():
            conn.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;"))
            conn.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY;"))
            conn.execute(sa.text(f"DROP POLICY IF EXISTS {policy_name} ON {table_name};"))
            conn.execute(sa.text(f"""
                CREATE POLICY {policy_name} ON {table_name} FOR ALL
                    USING (
                        CURRENT_SETTING('app.is_super_admin', TRUE) = 'true'
                        OR CURRENT_SETTING('app.is_system_context', TRUE) = 'true'
                        OR garage_id = NULLIF(CURRENT_SETTING('app.current_garage_id', TRUE), '')::INTEGER
                        OR (
                            NULLIF(CURRENT_SETTING('app.current_owner_id', TRUE), '') IS NOT NULL
                            AND garage_id IN (
                                SELECT id FROM garages WHERE owner_id = NULLIF(CURRENT_SETTING('app.current_owner_id', TRUE), '')::INTEGER
                            )
                        )
                    )
                    WITH CHECK (
                        CURRENT_SETTING('app.is_super_admin', TRUE) = 'true'
                        OR CURRENT_SETTING('app.is_system_context', TRUE) = 'true'
                        OR garage_id = NULLIF(CURRENT_SETTING('app.current_garage_id', TRUE), '')::INTEGER
                    );
            """))

        # Table 11: Dedicated RLS policy for audit_logs
        conn.execute(sa.text("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;"))
        conn.execute(sa.text("ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY;"))
        conn.execute(sa.text("DROP POLICY IF EXISTS rls_policy_audit ON audit_logs;"))
        conn.execute(sa.text("""
            CREATE POLICY rls_policy_audit ON audit_logs FOR ALL
                USING (
                    CURRENT_SETTING('app.is_super_admin', TRUE) = 'true'
                    OR CURRENT_SETTING('app.is_system_context', TRUE) = 'true'
                    OR (garage_id IS NOT NULL AND garage_id = NULLIF(CURRENT_SETTING('app.current_garage_id', TRUE), '')::INTEGER)
                    OR (
                        NULLIF(CURRENT_SETTING('app.current_owner_id', TRUE), '') IS NOT NULL
                        AND (
                            owner_id = NULLIF(CURRENT_SETTING('app.current_owner_id', TRUE), '')::INTEGER
                            OR garage_id IN (
                                SELECT id FROM garages WHERE owner_id = NULLIF(CURRENT_SETTING('app.current_owner_id', TRUE), '')::INTEGER
                            )
                        )
                    )
                )
                WITH CHECK (
                    CURRENT_SETTING('app.is_super_admin', TRUE) = 'true'
                    OR CURRENT_SETTING('app.is_system_context', TRUE) = 'true'
                    OR (garage_id IS NOT NULL AND garage_id = NULLIF(CURRENT_SETTING('app.current_garage_id', TRUE), '')::INTEGER)
                    OR (garage_id IS NULL AND (CURRENT_SETTING('app.is_super_admin', TRUE) = 'true' OR CURRENT_SETTING('app.current_owner_id', TRUE) IS NOT NULL))
                );
        """))


def downgrade() -> None:
    conn = op.get_bind()
    is_pg = conn.dialect.name == "postgresql"

    if is_pg:
        # 1. Disable RLS and drop policies
        conn.execute(sa.text("DROP POLICY IF EXISTS rls_policy_audit ON audit_logs;"))
        conn.execute(sa.text("ALTER TABLE audit_logs NO FORCE ROW LEVEL SECURITY;"))
        conn.execute(sa.text("ALTER TABLE audit_logs DISABLE ROW LEVEL SECURITY;"))

        for table_name, policy_name in RLS_TABLES_MAP.items():
            conn.execute(sa.text(f"DROP POLICY IF EXISTS {policy_name} ON {table_name};"))
            conn.execute(sa.text(f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY;"))
            conn.execute(sa.text(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY;"))

        # 2. Drop audit_logs constraints
        conn.execute(sa.text("ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS ck_audit_garage_context;"))
        conn.execute(sa.text("DROP INDEX IF EXISTS idx_audit_logs_owner_id;"))
        conn.execute(sa.text("DROP INDEX IF EXISTS idx_audit_logs_garage_id;"))

        # 3. Drop parking_sessions composite constraints and indexes
        conn.execute(sa.text("DROP INDEX IF EXISTS idx_sessions_garage_plate;"))
        conn.execute(sa.text("DROP INDEX IF EXISTS idx_sessions_garage_entry_time;"))
        conn.execute(sa.text("DROP INDEX IF EXISTS idx_sessions_garage_status;"))
        conn.execute(sa.text("DROP INDEX IF EXISTS idx_garage_active_card_id;"))
        conn.execute(sa.text("ALTER TABLE parking_sessions DROP CONSTRAINT IF EXISTS fk_sessions_subscription_garage;"))
        conn.execute(sa.text("ALTER TABLE parking_sessions DROP CONSTRAINT IF EXISTS fk_sessions_gate_garage;"))
        conn.execute(sa.text("ALTER TABLE parking_sessions DROP CONSTRAINT IF EXISTS fk_sessions_admin_override_garage;"))
        conn.execute(sa.text("ALTER TABLE parking_sessions DROP CONSTRAINT IF EXISTS fk_sessions_exit_operator_garage;"))
        conn.execute(sa.text("ALTER TABLE parking_sessions DROP CONSTRAINT IF EXISTS fk_sessions_operator_garage;"))
        conn.execute(sa.text("ALTER TABLE parking_sessions DROP CONSTRAINT IF EXISTS fk_sessions_pricing_garage;"))
        conn.execute(sa.text("ALTER TABLE parking_sessions DROP CONSTRAINT IF EXISTS fk_sessions_exit_shift_garage;"))
        conn.execute(sa.text("ALTER TABLE parking_sessions DROP CONSTRAINT IF EXISTS fk_sessions_entry_shift_garage;"))
        conn.execute(sa.text("ALTER TABLE parking_sessions DROP CONSTRAINT IF EXISTS fk_sessions_card_garage;"))
        conn.execute(sa.text("ALTER TABLE parking_sessions DROP CONSTRAINT IF EXISTS ck_session_card_or_ticket;"))
        conn.execute(sa.text("ALTER TABLE parking_sessions DROP CONSTRAINT IF EXISTS uq_garage_receipt_no;"))
        conn.execute(sa.text("ALTER TABLE parking_sessions DROP CONSTRAINT IF EXISTS uq_garage_ticket_code;"))

        # 4. Drop subscriptions composite FKs
        conn.execute(sa.text("DROP INDEX IF EXISTS idx_subscriptions_garage_status;"))
        conn.execute(sa.text("ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS fk_subscriptions_prev_sub;"))
        conn.execute(sa.text("ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS fk_subscriptions_collected_by;"))
        conn.execute(sa.text("ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS fk_subscriptions_card_garage;"))
        conn.execute(sa.text("ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS fk_subscriptions_plan_garage;"))
        conn.execute(sa.text("ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS fk_subscriptions_subscriber_garage;"))
        conn.execute(sa.text("ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS uq_subscriptions_id_garage;"))

        # 5. Drop subscription_plans composite FKs
        conn.execute(sa.text("DROP INDEX IF EXISTS idx_subscription_plans_garage;"))
        conn.execute(sa.text("ALTER TABLE subscription_plans DROP CONSTRAINT IF EXISTS fk_plans_created_by;"))
        conn.execute(sa.text("ALTER TABLE subscription_plans DROP CONSTRAINT IF EXISTS uq_garage_plan_label;"))
        conn.execute(sa.text("ALTER TABLE subscription_plans DROP CONSTRAINT IF EXISTS uq_subscription_plans_id_garage;"))

        # 6. Drop subscribers constraints
        conn.execute(sa.text("DROP INDEX IF EXISTS idx_subscribers_garage;"))
        conn.execute(sa.text("DROP INDEX IF EXISTS uq_garage_subscriber_card;"))
        conn.execute(sa.text("ALTER TABLE subscribers DROP CONSTRAINT IF EXISTS uq_garage_subscriber_plate;"))
        conn.execute(sa.text("ALTER TABLE subscribers DROP CONSTRAINT IF EXISTS uq_subscribers_id_garage;"))

        # 7. Drop pricing_rules composite FKs
        conn.execute(sa.text("DROP INDEX IF EXISTS idx_pricing_rules_active;"))
        conn.execute(sa.text("DROP INDEX IF EXISTS idx_pricing_rules_garage;"))
        conn.execute(sa.text("ALTER TABLE pricing_rules DROP CONSTRAINT IF EXISTS fk_pricing_created_by;"))
        conn.execute(sa.text("ALTER TABLE pricing_rules DROP CONSTRAINT IF EXISTS uq_garage_pricing_label;"))
        conn.execute(sa.text("ALTER TABLE pricing_rules DROP CONSTRAINT IF EXISTS uq_pricing_rules_id_garage;"))

        # 8. Drop shifts composite FKs
        conn.execute(sa.text("DROP INDEX IF EXISTS idx_shifts_garage_active;"))
        conn.execute(sa.text("DROP INDEX IF EXISTS idx_shifts_garage_lookup;"))
        conn.execute(sa.text("ALTER TABLE shifts DROP CONSTRAINT IF EXISTS fk_shifts_gate_garage;"))
        conn.execute(sa.text("ALTER TABLE shifts DROP CONSTRAINT IF EXISTS fk_shifts_operator_garage;"))
        conn.execute(sa.text("ALTER TABLE shifts DROP CONSTRAINT IF EXISTS uq_shifts_id_garage;"))

        # 9. Drop parking_cards constraints
        conn.execute(sa.text("DROP INDEX IF EXISTS idx_parking_cards_garage_status;"))
        conn.execute(sa.text("ALTER TABLE parking_cards DROP CONSTRAINT IF EXISTS uq_garage_card_code;"))
        conn.execute(sa.text("ALTER TABLE parking_cards DROP CONSTRAINT IF EXISTS uq_parking_cards_id_garage;"))

        # 10. Drop users constraints
        conn.execute(sa.text("DROP INDEX IF EXISTS idx_users_garage_lookup;"))
        conn.execute(sa.text("DROP INDEX IF EXISTS uq_global_users_username;"))
        conn.execute(sa.text("ALTER TABLE users DROP CONSTRAINT IF EXISTS uq_users_garage_username;"))
        conn.execute(sa.text("ALTER TABLE users DROP CONSTRAINT IF EXISTS uq_users_id_garage;"))

        # 11. Revert columns to nullable
        conn.execute(sa.text("ALTER TABLE subscriptions ALTER COLUMN garage_id DROP NOT NULL;"))
        conn.execute(sa.text("ALTER TABLE subscription_plans ALTER COLUMN garage_id DROP NOT NULL;"))
        conn.execute(sa.text("ALTER TABLE subscribers ALTER COLUMN garage_id DROP NOT NULL;"))
        conn.execute(sa.text("ALTER TABLE parking_sessions ALTER COLUMN receipt_no DROP NOT NULL;"))
        conn.execute(sa.text("ALTER TABLE parking_sessions ALTER COLUMN garage_id DROP NOT NULL;"))
        conn.execute(sa.text("ALTER TABLE pricing_rules ALTER COLUMN garage_id DROP NOT NULL;"))
        conn.execute(sa.text("ALTER TABLE shifts ALTER COLUMN garage_id DROP NOT NULL;"))
        conn.execute(sa.text("ALTER TABLE parking_cards ALTER COLUMN garage_id DROP NOT NULL;"))
