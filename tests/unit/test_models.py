import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy import inspect
from config import get_settings
from models import (
    Owner,
    Garage,
    GarageCounter,
    GarageGate,
    EdgeDevice,
    EdgeDeviceGate,
    GarageConfig,
    User,
    UserRole,
    PricingRule,
    Shift,
    ParkingCard,
    ParkingSession,
    AuditLog,
    SubscriptionPlan,
    Subscriber,
    Subscription,
)


@pytest_asyncio.fixture
async def test_engine():
    engine = create_async_engine(get_settings().DATABASE_URL, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_users_constraints_inspection(test_engine):
    def inspect_users(sync_conn):
        insp = inspect(sync_conn)
        check_constraints = [c["name"] for c in insp.get_check_constraints("users")]
        unique_constraints = [u["name"] for u in insp.get_unique_constraints("users")]
        return check_constraints, unique_constraints

    async with test_engine.connect() as conn:
        check_constraints, unique_constraints = await conn.run_sync(inspect_users)

    assert "ck_users_saas_hierarchy" in check_constraints
    assert "uq_users_id_garage" in unique_constraints
    assert "uq_users_garage_username" in unique_constraints


@pytest.mark.asyncio
async def test_owners_inspection(test_engine):
    def inspect_owners(sync_conn):
        insp = inspect(sync_conn)
        pk = insp.get_pk_constraint("owners")
        columns = [col["name"] for col in insp.get_columns("owners")]
        return pk, columns

    async with test_engine.connect() as conn:
        pk, columns = await conn.run_sync(inspect_owners)

    assert pk["constrained_columns"] == ["id"]
    assert "name" in columns
    assert "contact_email" in columns
    assert "billing_status" in columns
    assert "max_garages_allowed" in columns


@pytest.mark.asyncio
async def test_garages_constraints_inspection(test_engine):
    def inspect_garages(sync_conn):
        insp = inspect(sync_conn)
        return [u["name"] for u in insp.get_unique_constraints("garages")]

    async with test_engine.connect() as conn:
        unique_constraints = await conn.run_sync(inspect_garages)

    assert "uq_garages_id_owner" in unique_constraints


@pytest.mark.asyncio
async def test_garage_counters_inspection(test_engine):
    def inspect_counters(sync_conn):
        insp = inspect(sync_conn)
        return insp.get_pk_constraint("garage_counters")

    async with test_engine.connect() as conn:
        pk = await conn.run_sync(inspect_counters)

    assert pk["constrained_columns"] == ["garage_id"]


@pytest.mark.asyncio
async def test_garage_gates_constraints_inspection(test_engine):
    def inspect_gates(sync_conn):
        insp = inspect(sync_conn)
        return [u["name"] for u in insp.get_unique_constraints("garage_gates")]

    async with test_engine.connect() as conn:
        unique_constraints = await conn.run_sync(inspect_gates)

    assert "uq_garage_gate_number" in unique_constraints
    assert "uq_garage_gates_id_garage" in unique_constraints


@pytest.mark.asyncio
async def test_edge_device_gates_fks_inspection(test_engine):
    def inspect_edge_device_gates(sync_conn):
        insp = inspect(sync_conn)
        return [fk["name"] for fk in insp.get_foreign_keys("edge_device_gates")]

    async with test_engine.connect() as conn:
        fk_names = await conn.run_sync(inspect_edge_device_gates)

    assert "fk_edg_device" in fk_names
    assert "fk_edg_gate" in fk_names


@pytest.mark.asyncio
async def test_garage_configs_inspection(test_engine):
    def inspect_configs(sync_conn):
        insp = inspect(sync_conn)
        return [u["name"] for u in insp.get_unique_constraints("garage_configs")]

    async with test_engine.connect() as conn:
        unique_constraints = await conn.run_sync(inspect_configs)

    assert "uq_garage_configs_id_garage" in unique_constraints


@pytest.mark.asyncio
async def test_operational_tables_composite_fks(test_engine):
    def inspect_fks(sync_conn):
        insp = inspect(sync_conn)
        shift_fks = [fk["name"] for fk in insp.get_foreign_keys("shifts")]
        session_fks = [fk["name"] for fk in insp.get_foreign_keys("parking_sessions")]
        sub_fks = [fk["name"] for fk in insp.get_foreign_keys("subscriptions")]
        return shift_fks, session_fks, sub_fks

    async with test_engine.connect() as conn:
        shift_fks, session_fks, sub_fks = await conn.run_sync(inspect_fks)

    # shifts
    assert "fk_shifts_operator_garage" in shift_fks
    assert "fk_shifts_gate_garage" in shift_fks

    # parking_sessions
    assert "fk_sessions_card_garage" in session_fks
    assert "fk_sessions_entry_shift_garage" in session_fks
    assert "fk_sessions_exit_shift_garage" in session_fks
    assert "fk_sessions_pricing_garage" in session_fks
    assert "fk_sessions_operator_garage" in session_fks
    assert "fk_sessions_gate_garage" in session_fks

    # subscriptions
    assert "fk_subscriptions_subscriber_garage" in sub_fks
    assert "fk_subscriptions_plan_garage" in sub_fks
    assert "fk_subscriptions_card_garage" in sub_fks


@pytest.mark.asyncio
async def test_all_models_mapper_registry():
    import warnings
    warnings.simplefilter("error")
    from sqlalchemy.orm import configure_mappers
    configure_mappers()

    assert User.__mapper__ is not None
    assert Owner.__mapper__ is not None
    assert Garage.__mapper__ is not None
    assert GarageCounter.__mapper__ is not None
    assert GarageGate.__mapper__ is not None
    assert EdgeDevice.__mapper__ is not None
    assert EdgeDeviceGate.__mapper__ is not None
    assert GarageConfig.__mapper__ is not None
    assert PricingRule.__mapper__ is not None
    assert Shift.__mapper__ is not None
    assert ParkingCard.__mapper__ is not None
    assert ParkingSession.__mapper__ is not None
    assert AuditLog.__mapper__ is not None
    assert SubscriptionPlan.__mapper__ is not None
    assert Subscriber.__mapper__ is not None
    assert Subscription.__mapper__ is not None
