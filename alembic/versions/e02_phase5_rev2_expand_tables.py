"""Phase 5 Revision 2: Create SaaS core tables and expand existing tables with nullable tenant columns

Revision ID: e02_phase5_rev2
Revises: e01_phase5_rev1
Create Date: 2026-10-29 14:48:00.000000

ARCHITECTURAL NOTES:
- Expand phase of Expand/Contract pattern.
- Creates the 7 new SaaS core tables: owners, garages, garage_counters,
  garage_gates, edge_devices, edge_device_gates, garage_configs.
- Adds tenant context columns (garage_id, owner_id) as NULLABLE to existing tables
  so existing single-tenant data remains valid until backfilled in Revision 3.
- Relaxes NOT NULL on parking_sessions.card_id/card_code to prepare for paper barcode tickets.
- Relaxes NOT NULL on audit_logs.actor_id and adds actor_type to support system/edge audit events.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e02_phase5_rev2'
down_revision: Union[str, None] = 'e01_phase5_rev1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    is_pg = conn.dialect.name == "postgresql"

    # --------------------------------------------------------------------------
    # 1. Create New Core SaaS Tables
    # --------------------------------------------------------------------------

    # 1.1 Table: owners
    op.create_table(
        'owners',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('company_name', sa.String(length=150), nullable=True),
        sa.Column('contact_phone', sa.String(length=30), nullable=False),
        sa.Column('contact_email', sa.String(length=120), nullable=False, unique=True),
        sa.Column('subscription_plan', sa.String(length=40), server_default='STANDARD', nullable=False),
        sa.Column('billing_status', sa.String(length=30), server_default='ACTIVE', nullable=False),
        sa.Column('max_garages_allowed', sa.Integer(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint('max_garages_allowed > 0', name='ck_owners_max_garages'),
    )
    op.create_index('idx_owners_email', 'owners', ['contact_email'])
    op.create_index('idx_owners_billing_status', 'owners', ['billing_status'])

    # 1.2 Table: garages
    op.create_table(
        'garages',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('owners.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('code', sa.String(length=30), nullable=False, unique=True),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('city', sa.String(length=60), server_default='Cairo', nullable=False),
        sa.Column('capacity', sa.Integer(), nullable=False),
        sa.Column('timezone', sa.String(length=50), server_default='Africa/Cairo', nullable=False),
        sa.Column('currency', sa.String(length=10), server_default='EGP', nullable=False),
        sa.Column('status', sa.String(length=30), server_default='ACTIVE', nullable=False),
        sa.Column('can_use_anpr', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('can_use_subscriptions', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('max_gates', sa.SmallInteger(), server_default='2', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint('capacity > 0', name='ck_garages_capacity'),
        sa.CheckConstraint("status IN ('ACTIVE', 'SUSPENDED', 'TERMINATED')", name='ck_garages_status'),
        sa.CheckConstraint('max_gates BETWEEN 1 AND 10', name='ck_garages_max_gates'),
        sa.UniqueConstraint('id', 'owner_id', name='uq_garages_id_owner'),
    )
    op.create_index('idx_garages_owner_id', 'garages', ['owner_id'])
    op.create_index('idx_garages_code', 'garages', ['code'])
    op.create_index('idx_garages_status', 'garages', ['status'])

    # 1.3 Table: garage_counters
    op.create_table(
        'garage_counters',
        sa.Column('garage_id', sa.Integer(), sa.ForeignKey('garages.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('last_receipt_no', sa.Integer(), server_default='0', nullable=False),
    )

    # 1.4 Table: garage_gates
    op.create_table(
        'garage_gates',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('garage_id', sa.Integer(), sa.ForeignKey('garages.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('gate_number', sa.SmallInteger(), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('gate_type', sa.String(length=30), server_default='MANUAL_BIDIRECTIONAL', nullable=False),
        sa.Column('default_terminal_profile', sa.String(length=30), server_default='DESKTOP_PC', nullable=False),
        sa.Column('paper_width_mm', sa.SmallInteger(), server_default='80', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint('gate_number BETWEEN 1 AND 10', name='ck_garage_gates_gate_number'),
        sa.CheckConstraint('paper_width_mm IN (58, 80)', name='ck_garage_gates_paper_width'),
        sa.UniqueConstraint('garage_id', 'gate_number', name='uq_garage_gate_number'),
        sa.UniqueConstraint('id', 'garage_id', name='uq_garage_gates_id_garage'),
    )
    op.create_index('idx_garage_gates_lookup', 'garage_gates', ['garage_id', 'gate_number'])

    # 1.5 Table: edge_devices
    op.create_table(
        'edge_devices',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('garage_id', sa.Integer(), sa.ForeignKey('garages.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('device_name', sa.String(length=80), nullable=False),
        sa.Column('token_hash', sa.String(length=128), nullable=False, unique=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.UniqueConstraint('id', 'garage_id', name='uq_edge_devices_id_garage'),
    )
    op.create_index('idx_edge_devices_garage', 'edge_devices', ['garage_id'])

    # 1.6 Table: edge_device_gates
    op.create_table(
        'edge_device_gates',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.Column('garage_id', sa.Integer(), nullable=False),
        sa.Column('gate_number', sa.SmallInteger(), nullable=False),
        sa.Column('direction', sa.String(length=15), server_default='BIDIRECTIONAL', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint("direction IN ('ENTRY', 'EXIT', 'BIDIRECTIONAL')", name='ck_edg_direction'),
        sa.UniqueConstraint('device_id', 'garage_id', 'gate_number', 'direction', name='uq_device_gate_direction'),
        sa.UniqueConstraint('garage_id', 'gate_number', 'direction', name='uq_gate_direction_active'),
        sa.ForeignKeyConstraint(['device_id', 'garage_id'], ['edge_devices.id', 'edge_devices.garage_id'], name='fk_edg_device', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['garage_id', 'gate_number'], ['garage_gates.garage_id', 'garage_gates.gate_number'], name='fk_edg_gate', ondelete='RESTRICT'),
    )
    op.create_index('idx_edg_lookup', 'edge_device_gates', ['garage_id', 'gate_number'])

    # 1.7 Table: garage_configs
    json_col = postgresql.JSONB(astext_type=sa.Text()) if is_pg else sa.JSON()
    default_receipt_json = '{"header_title": "جراج سيارات", "header_subtitle": "أهلاً بكم", "footer_terms": "الجراج غير مسؤول عن محتويات السيارات الداخلية", "show_qr_code": true}'
    server_default_receipt = sa.text(f"'{default_receipt_json}'::jsonb") if is_pg else sa.text(f"'{default_receipt_json}'")

    op.create_table(
        'garage_configs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('garage_id', sa.Integer(), sa.ForeignKey('garages.id', ondelete='RESTRICT'), nullable=False, unique=True),
        sa.Column('ticket_system', sa.String(length=30), server_default='REUSABLE_CARDS', nullable=False),
        sa.Column('anpr_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('exit_photo_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('subscriptions_enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('require_shift_cash_reconciliation', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('min_session_duration_seconds', sa.SmallInteger(), server_default='60', nullable=False),
        sa.Column('receipt_settings', json_col, server_default=server_default_receipt, nullable=False),
        sa.Column('version', sa.Integer(), server_default='1', nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint('min_session_duration_seconds BETWEEN 30 AND 600', name='ck_garage_configs_min_duration'),
        sa.UniqueConstraint('id', 'garage_id', name='uq_garage_configs_id_garage'),
    )
    op.create_index('idx_garage_configs_garage', 'garage_configs', ['garage_id'])

    # --------------------------------------------------------------------------
    # 2. Expand Existing Tables with NULLABLE Columns
    # --------------------------------------------------------------------------

    # 2.1 users
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('owner_id', sa.Integer(), sa.ForeignKey('owners.id', ondelete='RESTRICT'), nullable=True))
        batch_op.add_column(sa.Column('garage_id', sa.Integer(), sa.ForeignKey('garages.id', ondelete='RESTRICT'), nullable=True))
        batch_op.add_column(sa.Column('must_change_password', sa.Boolean(), server_default=sa.text('false'), nullable=False))

    # 2.2 parking_cards
    with op.batch_alter_table('parking_cards', schema=None) as batch_op:
        batch_op.add_column(sa.Column('garage_id', sa.Integer(), sa.ForeignKey('garages.id', ondelete='RESTRICT'), nullable=True))

    # 2.3 shifts
    with op.batch_alter_table('shifts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('garage_id', sa.Integer(), sa.ForeignKey('garages.id', ondelete='RESTRICT'), nullable=True))

    # 2.4 pricing_rules
    with op.batch_alter_table('pricing_rules', schema=None) as batch_op:
        batch_op.add_column(sa.Column('garage_id', sa.Integer(), sa.ForeignKey('garages.id', ondelete='RESTRICT'), nullable=True))

    # 2.5 parking_sessions
    with op.batch_alter_table('parking_sessions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('garage_id', sa.Integer(), sa.ForeignKey('garages.id', ondelete='RESTRICT'), nullable=True))
        batch_op.add_column(sa.Column('receipt_no', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('ticket_code', sa.String(length=50), nullable=True))
        batch_op.alter_column('card_id', existing_type=sa.Integer(), nullable=True)
        batch_op.alter_column('card_code', existing_type=sa.String(length=50), nullable=True)

    # 2.6 subscribers
    with op.batch_alter_table('subscribers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('garage_id', sa.Integer(), sa.ForeignKey('garages.id', ondelete='RESTRICT'), nullable=True))

    # 2.7 subscription_plans
    with op.batch_alter_table('subscription_plans', schema=None) as batch_op:
        batch_op.add_column(sa.Column('garage_id', sa.Integer(), sa.ForeignKey('garages.id', ondelete='RESTRICT'), nullable=True))

    # 2.8 subscriptions
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('garage_id', sa.Integer(), sa.ForeignKey('garages.id', ondelete='RESTRICT'), nullable=True))

    # 2.9 audit_logs
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('garage_id', sa.Integer(), sa.ForeignKey('garages.id', ondelete='RESTRICT'), nullable=True))
        batch_op.add_column(sa.Column('owner_id', sa.Integer(), sa.ForeignKey('owners.id', ondelete='RESTRICT'), nullable=True))
        batch_op.add_column(sa.Column('actor_type', sa.String(length=20), server_default=sa.text("'user'"), nullable=False))
        batch_op.alter_column('actor_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # 1. Drop added columns and revert alterations
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.alter_column('actor_id', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column('actor_type')
        batch_op.drop_column('owner_id')
        batch_op.drop_column('garage_id')

    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.drop_column('garage_id')

    with op.batch_alter_table('subscription_plans', schema=None) as batch_op:
        batch_op.drop_column('garage_id')

    with op.batch_alter_table('subscribers', schema=None) as batch_op:
        batch_op.drop_column('garage_id')

    op.execute("DELETE FROM parking_sessions WHERE card_id IS NULL OR card_code IS NULL;")

    with op.batch_alter_table('parking_sessions', schema=None) as batch_op:
        batch_op.alter_column('card_code', existing_type=sa.String(length=50), nullable=False)
        batch_op.alter_column('card_id', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column('ticket_code')
        batch_op.drop_column('receipt_no')
        batch_op.drop_column('garage_id')

    with op.batch_alter_table('pricing_rules', schema=None) as batch_op:
        batch_op.drop_column('garage_id')

    with op.batch_alter_table('shifts', schema=None) as batch_op:
        batch_op.drop_column('garage_id')

    with op.batch_alter_table('parking_cards', schema=None) as batch_op:
        batch_op.drop_column('garage_id')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('must_change_password')
        batch_op.drop_column('garage_id')
        batch_op.drop_column('owner_id')

    # 2. Drop core SaaS tables in reverse dependency order
    op.drop_table('garage_configs')
    op.drop_table('edge_device_gates')
    op.drop_table('edge_devices')
    op.drop_table('garage_gates')
    op.drop_table('garage_counters')
    op.drop_table('garages')
    op.drop_table('owners')
