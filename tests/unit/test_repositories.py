import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.pool import NullPool

from config import get_settings
from models.owner import Owner
from models.garage import Garage
from models.garage_gate import GarageGate
from models.garage_config import GarageConfig
from models.user import User, UserRole
from models.pricing_rule import PricingRule
from models.parking_card import ParkingCard, CardStatus
from models.shift import Shift
from models.parking_session import ParkingSession, SessionStatus
from models.edge_device import EdgeDevice

from repositories.owner_repo import OwnerRepository
from repositories.garage_repo import GarageRepository
from repositories.garage_gate_repo import GarageGateRepository
from repositories.garage_config_repo import GarageConfigRepository
from repositories.edge_device_repo import EdgeDeviceRepository
from repositories.counter_repo import GarageCounterRepository
from repositories.rate_repo import PricingRuleRepository
from repositories.card_repo import ParkingCardRepository
from repositories.shift_repo import ShiftRepository
from repositories.session_repo import ParkingSessionRepository
from repositories.user_repo import UserRepository
from repositories.report_repo import ReportRepository


@pytest_asyncio.fixture
async def repo_session():
    """Provides an isolated AsyncSession against PostgreSQL.
    Every test runs in a transaction that is rolled back at completion,
    leaving the database completely unmodified.
    """
    settings = get_settings()
    db_url = settings.MIGRATION_DATABASE_URL or settings.DATABASE_URL
    engine = create_async_engine(db_url, poolclass=NullPool)
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        # Set system context for test setups
        await conn.execute(text("SELECT set_config('app.is_system_context', 'true', true);"))
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def test_env(repo_session: AsyncSession):
    """Creates two isolated owners and garages for cross-tenant testing."""
    owner_repo = OwnerRepository(repo_session)
    garage_repo = GarageRepository(repo_session)
    gate_repo = GarageGateRepository(repo_session)

    # 1. Create two owners
    owner1 = await owner_repo.create(
        name="Owner One",
        contact_phone="01000000001",
        contact_email="owner1_test@example.com",
        billing_status="ACTIVE",
        max_garages_allowed=5,
    )
    owner2 = await owner_repo.create(
        name="Owner Two",
        contact_phone="01000000002",
        contact_email="owner2_test@example.com",
        billing_status="ACTIVE",
        max_garages_allowed=5,
    )

    # 2. Create two garages
    garage1 = await garage_repo.create(
        name="Garage One",
        code="GAR01",
        owner_id=owner1.id,
        capacity=100,
        status="ACTIVE",
    )
    garage2 = await garage_repo.create(
        name="Garage Two",
        code="GAR02",
        owner_id=owner2.id,
        capacity=200,
        status="ACTIVE",
    )

    # 3. Create gates for each garage
    gate1 = await gate_repo.create(
        garage_id=garage1.id,
        gate_number=1,
        gate_type="BIDIRECTIONAL",
        name="Gate 1-1",
    )
    gate2 = await gate_repo.create(
        garage_id=garage2.id,
        gate_number=1,
        gate_type="BIDIRECTIONAL",
        name="Gate 2-1",
    )

    return {
        "owner1": owner1,
        "owner2": owner2,
        "garage1": garage1,
        "garage2": garage2,
        "gate1": gate1,
        "gate2": gate2,
    }


# ==============================================================================
# 1. Cross-Tenant Pricing Isolation: set_active between two garages (Task 5.2)
# ==============================================================================
@pytest.mark.asyncio
async def test_pricing_rule_cross_tenant_isolation(repo_session: AsyncSession, test_env: dict):
    g1_id = test_env["garage1"].id
    g2_id = test_env["garage2"].id
    rate_repo = PricingRuleRepository(repo_session)
    user_repo = UserRepository(repo_session)

    # Need creator users satisfying fk_pricing_created_by (manager per garage)
    creator_g1 = await user_repo.create(
        username="creator_g1",
        full_name="Creator G1",
        hashed_password="pw",
        role=UserRole.MANAGER,
        owner_id=test_env["owner1"].id,
        garage_id=g1_id,
        is_active=True,
    )
    creator_g2 = await user_repo.create(
        username="creator_g2",
        full_name="Creator G2",
        hashed_password="pw",
        role=UserRole.MANAGER,
        owner_id=test_env["owner2"].id,
        garage_id=g2_id,
        is_active=True,
    )

    now = datetime.utcnow()

    # Garage 1: Rule 1A (Active initially), Rule 1B (Inactive)
    rule_1a = await rate_repo.create(
        garage_id=g1_id,
        label="Standard G1 A",
        rate_per_hour=10,
        created_by=creator_g1.id,
        effective_from=now,
        lost_card_penalty=50,
        is_active=True,
    )
    rule_1b = await rate_repo.create(
        garage_id=g1_id,
        label="Night G1 B",
        rate_per_hour=15,
        created_by=creator_g1.id,
        effective_from=now,
        lost_card_penalty=50,
        is_active=False,
    )

    # Garage 2: Rule 2A (Active initially)
    rule_2a = await rate_repo.create(
        garage_id=g2_id,
        label="Standard G2 A",
        rate_per_hour=20,
        created_by=creator_g2.id,
        effective_from=now,
        lost_card_penalty=100,
        is_active=True,
    )

    # Action: Activate Rule 1B in Garage 1
    activated_1b = await rate_repo.set_active(rule_id=rule_1b.id, garage_id=g1_id)
    assert activated_1b.is_active is True

    # Verification Garage 1: 1B is active, 1A is now inactive
    refreshed_1a = await rate_repo.get_by_id(rule_1a.id, garage_id=g1_id)
    refreshed_1b = await rate_repo.get_by_id(rule_1b.id, garage_id=g1_id)
    assert refreshed_1a.is_active is False
    assert refreshed_1b.is_active is True

    # Verification Garage 2: Rule 2A MUST REMAIN ACTIVE! (Zero leakage across tenants)
    refreshed_2a = await rate_repo.get_by_id(rule_2a.id, garage_id=g2_id)
    assert refreshed_2a.is_active is True
    active_g2 = await rate_repo.get_active(garage_id=g2_id)
    assert active_g2 is not None
    assert active_g2.id == rule_2a.id


# ==============================================================================
# 2. Atomic Receipt Counter Accuracy (Task 5.1)
# ==============================================================================
@pytest.mark.asyncio
async def test_atomic_receipt_counter_accuracy(repo_session: AsyncSession, test_env: dict):
    g1_id = test_env["garage1"].id
    g2_id = test_env["garage2"].id
    counter_repo = GarageCounterRepository(repo_session)

    # Garage 1 increments sequentially: 1, 2, 3
    r1_g1 = await counter_repo.get_next_receipt_no(g1_id)
    r2_g1 = await counter_repo.get_next_receipt_no(g1_id)
    r3_g1 = await counter_repo.get_next_receipt_no(g1_id)

    assert r1_g1 == 1
    assert r2_g1 == 2
    assert r3_g1 == 3

    # Garage 2 increments independently: starts at 1
    r1_g2 = await counter_repo.get_next_receipt_no(g2_id)
    assert r1_g2 == 1

    # Garage 1 current remains 3, Garage 2 current is 1
    assert await counter_repo.get_current(g1_id) == 3
    assert await counter_repo.get_current(g2_id) == 1


# ==============================================================================
# 3. User Scoping & IDOR Prevention in user_repo (Task 5.6)
# ==============================================================================
@pytest.mark.asyncio
async def test_user_scoping_and_idor_prevention(repo_session: AsyncSession, test_env: dict):
    g1_id = test_env["garage1"].id
    g2_id = test_env["garage2"].id
    owner1_id = test_env["owner1"].id
    user_repo = UserRepository(repo_session)

    # Create Operator in Garage 1 (Operator requires gate_number between 1 and 10)
    user1 = await user_repo.create(
        username="op_garage1",
        full_name="Operator Garage 1",
        hashed_password="hashed_pw_1",
        role=UserRole.OPERATOR,
        gate_number=1,
        owner_id=owner1_id,
        garage_id=g1_id,
        is_active=True,
    )

    # 1. Querying with matching garage_id succeeds
    found = await user_repo.get_by_id(user1.id, garage_id=g1_id)
    assert found is not None
    assert found.username == "op_garage1"

    # 2. Querying User 1 with Garage 2's ID returns None (Strict IDOR prevention)
    cross_tenant_lookup = await user_repo.get_by_id(user1.id, garage_id=g2_id)
    assert cross_tenant_lookup is None

    # 3. Querying username with wrong garage returns None
    wrong_garage_username = await user_repo.get_by_username("op_garage1", garage_id=g2_id)
    assert wrong_garage_username is None

    # 4. Querying username with correct garage returns user
    correct_username = await user_repo.get_by_username("op_garage1", garage_id=g1_id)
    assert correct_username is not None
    assert correct_username.id == user1.id


# ==============================================================================
# 4. Card Scoping: Same Card Code in Two Garages (Task 5.4)
# ==============================================================================
@pytest.mark.asyncio
async def test_card_scoping_duplicate_codes(repo_session: AsyncSession, test_env: dict):
    g1_id = test_env["garage1"].id
    g2_id = test_env["garage2"].id
    card_repo = ParkingCardRepository(repo_session)

    # Create card "CARD_999" in both Garage 1 and Garage 2
    c1 = await card_repo.create(card_code="CARD_999", garage_id=g1_id)
    c2 = await card_repo.create(card_code="CARD_999", garage_id=g2_id)

    assert c1.id != c2.id
    assert c1.card_code == c2.card_code

    # Scoped queries return exactly the card for the queried garage
    fetched_c1 = await card_repo.get_by_code("CARD_999", garage_id=g1_id)
    fetched_c2 = await card_repo.get_by_code("CARD_999", garage_id=g2_id)

    assert fetched_c1 is not None and fetched_c1.id == c1.id
    assert fetched_c2 is not None and fetched_c2.id == c2.id


# ==============================================================================
# 5. Garage Config: Optimistic Locking (Task 5.1)
# ==============================================================================
@pytest.mark.asyncio
async def test_garage_config_optimistic_locking(repo_session: AsyncSession, test_env: dict):
    g1_id = test_env["garage1"].id
    config_repo = GarageConfigRepository(repo_session)

    # Create config for Garage 1 (starts at version 1)
    cfg = await config_repo.create(
        garage_id=g1_id,
        ticket_system="RFID_ONLY",
        min_session_duration_seconds=60,
        version=1,
    )
    assert cfg.version == 1

    # First update with expected_version=1 succeeds -> version becomes 2
    updated_cfg = await config_repo.update_with_optimistic_lock(
        garage_id=g1_id,
        expected_version=1,
        min_session_duration_seconds=120,
    )
    assert updated_cfg is not None
    assert updated_cfg.version == 2
    assert updated_cfg.min_session_duration_seconds == 120

    # Stale update with expected_version=1 fails (returns None)
    stale_update = await config_repo.update_with_optimistic_lock(
        garage_id=g1_id,
        expected_version=1,
        min_session_duration_seconds=180,
    )
    assert stale_update is None

    # Update with correct expected_version=2 succeeds -> version becomes 3
    fresh_update = await config_repo.update_with_optimistic_lock(
        garage_id=g1_id,
        expected_version=2,
        min_session_duration_seconds=180,
    )
    assert fresh_update is not None
    assert fresh_update.version == 3
    assert fresh_update.min_session_duration_seconds == 180


# ==============================================================================
# 6. Edge Device: Global Handshake vs Scoped Operations (Task 5.1)
# ==============================================================================
@pytest.mark.asyncio
async def test_edge_device_global_and_scoped(repo_session: AsyncSession, test_env: dict):
    g1_id = test_env["garage1"].id
    g2_id = test_env["garage2"].id
    edge_repo = EdgeDeviceRepository(repo_session)

    token_hash = "hash_token_abc_123"
    dev = await edge_repo.create(
        garage_id=g1_id,
        device_name="Gate 1 Camera",
        token_hash=token_hash,
    )

    # 1. Global lookup by token hash without garage context succeeds
    found_global = await edge_repo.get_by_token_hash(token_hash)
    assert found_global is not None
    assert found_global.id == dev.id

    # 2. Scoped query by garage
    devices_g1 = await edge_repo.get_by_garage(g1_id)
    assert len(devices_g1) == 1
    assert devices_g1[0].id == dev.id

    devices_g2 = await edge_repo.get_by_garage(g2_id)
    assert len(devices_g2) == 0

    # 3. Revoke device
    revoked = await edge_repo.revoke(device_id=dev.id, garage_id=g1_id)
    assert revoked is not None
    assert revoked.revoked_at is not None

    # Revoked device is no longer found via global handshake
    assert await edge_repo.get_by_token_hash(token_hash) is None


# ==============================================================================
# 7. Scoped Shifts and Sessions (Task 5.5)
# ==============================================================================
@pytest.mark.asyncio
async def test_shifts_and_sessions_scoped(repo_session: AsyncSession, test_env: dict):
    g1_id = test_env["garage1"].id
    g2_id = test_env["garage2"].id
    gate1 = test_env["gate1"]
    gate2 = test_env["gate2"]
    owner1_id = test_env["owner1"].id

    user_repo = UserRepository(repo_session)
    shift_repo = ShiftRepository(repo_session)
    session_repo = ParkingSessionRepository(repo_session)
    card_repo = ParkingCardRepository(repo_session)
    counter_repo = GarageCounterRepository(repo_session)

    op1 = await user_repo.create(
        username="operator_shift",
        full_name="Operator Shift",
        hashed_password="pw",
        role=UserRole.OPERATOR,
        gate_number=1,
        owner_id=owner1_id,
        garage_id=g1_id,
        is_active=True,
    )

    # Create active shift in Garage 1
    shift1 = await shift_repo.create(
        garage_id=g1_id,
        operator_id=op1.id,
        gate_number=gate1.gate_number,
        started_at=datetime.utcnow(),
        opening_cash_egp=500,
    )

    # Active shift lookup scoped to Garage 1 succeeds
    s1 = await shift_repo.get_active_for_operator(op1.id, garage_id=g1_id)
    assert s1 is not None
    assert s1.id == shift1.id

    # Active shift lookup with Garage 2 returns None
    assert await shift_repo.get_active_for_operator(op1.id, garage_id=g2_id) is None

    # Create card & session in Garage 1
    c1 = await card_repo.create(card_code="CARD_SESS_1", garage_id=g1_id)
    receipt_no = await counter_repo.get_next_receipt_no(g1_id)

    sess = await session_repo.create(
        garage_id=g1_id,
        receipt_no=receipt_no,
        gate_number=gate1.gate_number,
        shift_id=shift1.id,
        operator_id=op1.id,
        card_id=c1.id,
        card_code=c1.card_code,
        ticket_code="TKT-12345",
        status=SessionStatus.ACTIVE,
        entry_time=datetime.utcnow(),
    )

    # Query active session by card_id scoped to Garage 1 vs Garage 2
    assert await session_repo.get_active_by_card_id(c1.id, garage_id=g1_id) is not None
    assert await session_repo.get_active_by_card_id(c1.id, garage_id=g2_id) is None

    # Query active session by ticket_code scoped to Garage 1 vs Garage 2
    assert await session_repo.get_active_by_ticket_code("TKT-12345", garage_id=g1_id) is not None
    assert await session_repo.get_active_by_ticket_code("TKT-12345", garage_id=g2_id) is None


# ==============================================================================
# 8. Report Scoping (Task 5.3)
# ==============================================================================
@pytest.mark.asyncio
async def test_report_scoping(repo_session: AsyncSession, test_env: dict):
    g1_id = test_env["garage1"].id
    g2_id = test_env["garage2"].id
    gate1 = test_env["gate1"]
    gate2 = test_env["gate2"]
    owner1_id = test_env["owner1"].id
    owner2_id = test_env["owner2"].id

    user_repo = UserRepository(repo_session)
    shift_repo = ShiftRepository(repo_session)
    session_repo = ParkingSessionRepository(repo_session)
    counter_repo = GarageCounterRepository(repo_session)
    report_repo = ReportRepository(repo_session)

    # Garage 1: 1 operator, 1 shift, 1 active session
    op1 = await user_repo.create(
        username="op_rep_1",
        full_name="Op Rep 1",
        hashed_password="pw",
        role=UserRole.OPERATOR,
        gate_number=1,
        owner_id=owner1_id,
        garage_id=g1_id,
        is_active=True,
    )
    shift1 = await shift_repo.create(
        garage_id=g1_id,
        operator_id=op1.id,
        gate_number=gate1.gate_number,
        started_at=datetime.utcnow(),
    )
    r1 = await counter_repo.get_next_receipt_no(g1_id)
    await session_repo.create(
        garage_id=g1_id,
        receipt_no=r1,
        gate_number=gate1.gate_number,
        shift_id=shift1.id,
        operator_id=op1.id,
        ticket_code="TKT-G1",
        status=SessionStatus.ACTIVE,
        entry_time=datetime.utcnow(),
    )

    # Garage 2: 1 operator, 1 shift, 2 active sessions
    op2 = await user_repo.create(
        username="op_rep_2",
        full_name="Op Rep 2",
        hashed_password="pw",
        role=UserRole.OPERATOR,
        gate_number=1,
        owner_id=owner2_id,
        garage_id=g2_id,
        is_active=True,
    )
    shift2 = await shift_repo.create(
        garage_id=g2_id,
        operator_id=op2.id,
        gate_number=gate2.gate_number,
        started_at=datetime.utcnow(),
    )
    r2_a = await counter_repo.get_next_receipt_no(g2_id)
    r2_b = await counter_repo.get_next_receipt_no(g2_id)
    await session_repo.create(
        garage_id=g2_id,
        receipt_no=r2_a,
        gate_number=gate2.gate_number,
        shift_id=shift2.id,
        operator_id=op2.id,
        ticket_code="TKT-G2A",
        status=SessionStatus.ACTIVE,
        entry_time=datetime.utcnow(),
    )
    await session_repo.create(
        garage_id=g2_id,
        receipt_no=r2_b,
        gate_number=gate2.gate_number,
        shift_id=shift2.id,
        operator_id=op2.id,
        ticket_code="TKT-G2B",
        status=SessionStatus.ACTIVE,
        entry_time=datetime.utcnow(),
    )

    # Scoped active session counts
    assert await report_repo.count_active_sessions(garage_id=g1_id) == 1
    assert await report_repo.count_active_sessions(garage_id=g2_id) == 2
    assert await report_repo.count_open_shifts(garage_id=g1_id) == 1
    assert await report_repo.count_open_shifts(garage_id=g2_id) == 1


# ==============================================================================
# 9. Owner & Garage Repositories: CRUD & Owner IDOR Check (Tasks 5.1 & 5.6)
# ==============================================================================
@pytest.mark.asyncio
async def test_owner_and_garage_repo_idor(repo_session: AsyncSession, test_env: dict):
    owner_repo = OwnerRepository(repo_session)
    garage_repo = GarageRepository(repo_session)

    o1 = test_env["owner1"]
    o2 = test_env["owner2"]
    g1 = test_env["garage1"]

    # 1. Owner can add garage check
    can_add = await owner_repo.can_add_garage(o1.id)
    assert can_add is True

    # 2. Owner IDOR check on garage:
    # Querying Garage 1 with Owner 1 succeeds
    assert await garage_repo.get_by_id(g1.id, owner_id=o1.id) is not None

    # Querying Garage 1 with Owner 2 returns None (IDOR protected)
    assert await garage_repo.get_by_id(g1.id, owner_id=o2.id) is None

    # Count garages by owner
    assert await garage_repo.count_by_owner(o1.id) == 1
    assert await garage_repo.count_by_owner(o2.id) == 1
