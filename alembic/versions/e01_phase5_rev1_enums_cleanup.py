"""Phase 5 Revision 1: Expand userrole enum and drop legacy check constraint

Revision ID: e01_phase5_rev1
Revises: c9f5e1a82d06
Create Date: 2026-10-29 14:45:00.000000

ARCHITECTURAL NOTES:
- Expanding PostgreSQL enum 'userrole' with 'SUPER_ADMIN', 'OWNER', 'MANAGER'.
- The legacy 'ADMIN' value remains in the enum as documented Technical Debt (B4)
  because PostgreSQL does not support dropping individual enum values without
  recreating the enum type. Existing accounts will be migrated to 'MANAGER' in Revision 4.
- Dropping 'ck_users_role_gate_consistency' to unblock SaaS hierarchy transition.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e01_phase5_rev1'
down_revision: Union[str, None] = 'c9f5e1a82d06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    
    # 1. Expand userrole enum (PostgreSQL only)
    if connection.dialect.name == "postgresql":
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'SUPER_ADMIN'")
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'OWNER'")
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'MANAGER'")

    # 2. Drop legacy role-gate consistency constraint
    if connection.dialect.name == "postgresql":
        op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role_gate_consistency")
    else:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.drop_constraint("ck_users_role_gate_consistency", type_="check")


def downgrade() -> None:
    connection = op.get_bind()
    # Note: Enum values cannot be removed in PostgreSQL without recreating the enum type.
    # Re-apply legacy constraint on downgrade:
    if connection.dialect.name == "postgresql":
        op.execute("""
            ALTER TABLE users ADD CONSTRAINT ck_users_role_gate_consistency CHECK (
                (role = 'ADMIN' AND gate_number IS NULL) OR
                (role = 'OPERATOR' AND gate_number BETWEEN 1 AND 5)
            )
        """)
    else:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.create_check_constraint(
                "ck_users_role_gate_consistency",
                "(role = 'ADMIN' AND gate_number IS NULL) OR (role = 'OPERATOR' AND gate_number BETWEEN 1 AND 5)"
            )
