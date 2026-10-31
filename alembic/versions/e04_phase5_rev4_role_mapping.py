"""Phase 5 Revision 4: Clean users data, map roles to MANAGER/OPERATOR, and enforce SaaS hierarchy check constraint

Revision ID: e04_phase5_rev4
Revises: e03_phase5_rev3
Create Date: 2026-10-29 15:00:00.000000

ARCHITECTURAL NOTES:
- Transforms existing legacy 'ADMIN' account to 'MANAGER' scoped to Babelooq garage (owner_id=1, garage_id=1).
- Binds existing operators to Babelooq garage (owner_id=1, garage_id=1).
- Audits and handles unassigned operators safely before applying constraints.
- Applies strict 4-tier SaaS hierarchy constraint 'ck_users_saas_hierarchy' via Two-Phase safe constraint.
"""
import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from config import get_settings

logger = logging.getLogger("alembic.runtime.migration")

# revision identifiers, used by Alembic.
revision: str = 'e04_phase5_rev4'
down_revision: Union[str, None] = 'e03_phase5_rev3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    is_pg = conn.dialect.name == "postgresql"
    settings = get_settings()

    # 1. Map existing ADMIN users to MANAGER scoped to Babelooq garage
    conn.execute(sa.text("""
        UPDATE users 
        SET role = 'MANAGER', owner_id = 1, garage_id = 1, gate_number = NULL 
        WHERE role = 'ADMIN' OR username = 'admin';
    """))

    # 2. Inspect operators for invalid/missing gate assignments
    unassigned_count = conn.execute(sa.text("""
        SELECT count(*) FROM users 
        WHERE role = 'OPERATOR' AND (gate_number IS NULL OR gate_number NOT BETWEEN 1 AND 10);
    """)).scalar() or 0

    if unassigned_count > 0:
        logger.warning("Found %d unassigned operator(s) lacking valid gate_number (1-10).", unassigned_count)
        if settings.ENVIRONMENT == "production":
            raise RuntimeError(
                f"Found {unassigned_count} unassigned operator(s) in production without a valid gate_number. "
                "Manual administrative inspection required before applying hierarchy constraint."
            )
        else:
            # Fallback for dev/test: assign unassigned operators to gate 1
            conn.execute(sa.text("""
                UPDATE users 
                SET owner_id = 1, garage_id = 1, gate_number = 1 
                WHERE role = 'OPERATOR' AND (gate_number IS NULL OR gate_number NOT BETWEEN 1 AND 10);
            """))

    # 3. Scope valid operators to Babelooq garage
    conn.execute(sa.text("""
        UPDATE users 
        SET owner_id = 1, garage_id = 1 
        WHERE role = 'OPERATOR' AND gate_number BETWEEN 1 AND 10;
    """))

    # 4. Programmatic pre-constraint verification
    invalid_users_count = conn.execute(sa.text("""
        SELECT count(*) FROM users WHERE NOT (
            (role = 'SUPER_ADMIN' AND owner_id IS NULL AND garage_id IS NULL AND gate_number IS NULL) OR
            (role = 'OWNER' AND owner_id IS NOT NULL AND garage_id IS NULL AND gate_number IS NULL) OR
            (role = 'MANAGER' AND owner_id IS NOT NULL AND garage_id IS NOT NULL AND gate_number IS NULL) OR
            (role = 'OPERATOR' AND owner_id IS NOT NULL AND garage_id IS NOT NULL AND gate_number IS NOT NULL AND gate_number BETWEEN 1 AND 10)
        );
    """)).scalar() or 0

    if invalid_users_count > 0:
        raise RuntimeError(
            f"Pre-constraint validation failed: {invalid_users_count} user row(s) do not conform to "
            "SaaS role hierarchy before applying ck_users_saas_hierarchy."
        )

    # 5. Apply two-phase safe constraint on PostgreSQL (or standard batch constraint on SQLite)
    if is_pg:
        conn.execute(sa.text("""
            ALTER TABLE users ADD CONSTRAINT ck_users_saas_hierarchy CHECK (
                (role = 'SUPER_ADMIN' AND owner_id IS NULL AND garage_id IS NULL AND gate_number IS NULL) OR
                (role = 'OWNER' AND owner_id IS NOT NULL AND garage_id IS NULL AND gate_number IS NULL) OR
                (role = 'MANAGER' AND owner_id IS NOT NULL AND garage_id IS NOT NULL AND gate_number IS NULL) OR
                (role = 'OPERATOR' AND owner_id IS NOT NULL AND garage_id IS NOT NULL AND gate_number IS NOT NULL AND gate_number BETWEEN 1 AND 10)
            ) NOT VALID;
        """))
        conn.execute(sa.text("ALTER TABLE users VALIDATE CONSTRAINT ck_users_saas_hierarchy;"))
    else:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.create_check_constraint(
                "ck_users_saas_hierarchy",
                "(role = 'SUPER_ADMIN' AND owner_id IS NULL AND garage_id IS NULL AND gate_number IS NULL) OR "
                "(role = 'OWNER' AND owner_id IS NOT NULL AND garage_id IS NULL AND gate_number IS NULL) OR "
                "(role = 'MANAGER' AND owner_id IS NOT NULL AND garage_id IS NOT NULL AND gate_number IS NULL) OR "
                "(role = 'OPERATOR' AND owner_id IS NOT NULL AND garage_id IS NOT NULL AND gate_number IS NOT NULL AND gate_number BETWEEN 1 AND 10)"
            )


def downgrade() -> None:
    conn = op.get_bind()
    is_pg = conn.dialect.name == "postgresql"

    # 1. Drop hierarchy constraint
    if is_pg:
        conn.execute(sa.text("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_saas_hierarchy;"))
    else:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.drop_constraint("ck_users_saas_hierarchy", type_="check")

    # 2. Revert MANAGER to ADMIN
    conn.execute(sa.text("""
        UPDATE users 
        SET role = 'ADMIN', owner_id = NULL, garage_id = NULL 
        WHERE role = 'MANAGER' OR username = 'admin';
    """))

    # 3. Unassign owner_id and garage_id from operators
    conn.execute(sa.text("""
        UPDATE users 
        SET owner_id = NULL, garage_id = NULL 
        WHERE role = 'OPERATOR';
    """))
