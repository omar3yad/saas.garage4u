import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.pool import NullPool

from config import get_settings
from models.user import User, UserRole
from models.owner import Owner
from models.garage import Garage
from models.garage_gate import GarageGate
from models.garage_config import GarageConfig
from models.pricing_rule import PricingRule
from models.parking_card import ParkingCard, CardStatus
from models.parking_session import ParkingSession, SessionStatus
from models.shift import Shift
from models.audit_log import AuditLog

from repositories.garage_repo import GarageRepository
from repositories.garage_config_repo import GarageConfigRepository
from repositories.garage_gate_repo import GarageGateRepository
from repositories.counter_repo import GarageCounterRepository
from repositories.session_repo import ParkingSessionRepository
from repositories.card_repo import ParkingCardRepository
from repositories.rate_repo import PricingRuleRepository
from repositories.shift_repo import ShiftRepository
from repositories.report_repo import ReportRepository

from services.counter_service import CounterService
from services.config_service import ConfigService
from services.session_service import SessionService
from services.card_service import CardService
from services.pricing_service import PricingService
from services.shift_service import ShiftService
from services.report_service import ReportService
from services.audit_service import AuditService
from services.plate_service import PlateService
from services.auth_service import AuthService
from services.exceptions import (
    TenantNotFoundError,
    TenantSuspendedError,
    BillingPastDueError,
    TenantTerminatedError,
    FeatureNotEntitledError,
    ExitPhotoRequiresANPRError,
    ConfigLockedActiveSessionsError,
    ConfigVersionConflictError,
    ShortSessionConfirmationRequiredError,
    RapidDoubleScanError,
    SessionNotActiveError,
    SessionNotFoundError,
)


@pytest_asyncio.fixture
async def db_session():
    """Provides an isolated AsyncSession against PostgreSQL.
    Transactions are rolled back at completion leaving the DB clean.
    """
    settings = get_settings()
    db_url = settings.MIGRATION_DATABASE_URL or settings.DATABASE_URL
    engine = create_async_engine(db_url, poolclass=NullPool)
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        # Enable system context for test setups
        await conn.execute(text("SELECT set_config('app.is_system_context', 'true', true);"))
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def services_env(db_session: AsyncSession):
    """Sets up a complete garage environment with Owner, Garage, 8 Gates, Config,
    Pricing Rule, Operator, and an Active Shift.
    """
    # 1. Owner
    owner = Owner(
        name="Business Services Owner",
        company_name="Services Core Corp",
        contact_phone="+201009999999",
        contact_email="core_services@garage4u.com",
        subscription_plan="ENTERPRISE",
        billing_status="ACTIVE",
        max_garages_allowed=5,
    )
    db_session.add(owner)
    await db_session.flush()

    # 2. Garage
    garage = Garage(
        owner_id=owner.id,
        code="CORE-SRV-01",
        name="Core Business Test Garage",
        address="Downtown Services Hub",
        city="Cairo",
        capacity=150,
        status="ACTIVE",
        can_use_anpr=True,
        can_use_subscriptions=True,
        max_gates=8,
    )
    db_session.add(garage)
    await db_session.flush()

    # 3. 8 Gates (to test dynamic gate reporting)
    gates = []
    for g_num in range(1, 9):
        gate = GarageGate(
            garage_id=garage.id,
            gate_number=g_num,
            name=f"Gate {g_num}",
            gate_type="MANUAL_BIDIRECTIONAL" if g_num == 1 else "ENTRY" if g_num % 2 == 1 else "EXIT",
            paper_width_mm=80,
        )
        db_session.add(gate)
        gates.append(gate)
    await db_session.flush()

    # 4. Garage Config
    config = GarageConfig(
        garage_id=garage.id,
        ticket_system="REUSABLE_CARDS",
        anpr_enabled=True,
        exit_photo_enabled=False,
        subscriptions_enabled=True,
        require_shift_cash_reconciliation=True,
        min_session_duration_seconds=60,
        version=1,
    )
    db_session.add(config)
    await db_session.flush()

    # 5. Operator
    auth_svc = AuthService(get_settings())
    operator = User(
        username=f"srv_op_{garage.id}",
        full_name="Service Operator One",
        role=UserRole.OPERATOR,
        garage_id=garage.id,
        owner_id=owner.id,
        gate_number=1,
        hashed_password=auth_svc.hash_password("OperatorPass2026!"),
        is_active=True,
        must_change_password=False,
    )
    db_session.add(operator)
    await db_session.flush()

    # 6. Pricing Rule
    pricing_rule = PricingRule(
        garage_id=garage.id,
        label="Standard Business Rate",
        rate_per_hour=1000,
        first_hour_charge=2000,
        subsequent_hour_charge=1000,
        minimum_charge=2000,
        grace_period_mins=10,
        lost_card_penalty=5000,
        is_active=True,
        created_by=operator.id,
        effective_from=datetime.utcnow() - timedelta(days=1),
    )
    db_session.add(pricing_rule)
    await db_session.flush()

    # 7. Active Shift
    shift = Shift(
        garage_id=garage.id,
        operator_id=operator.id,
        gate_number=1,
        started_at=datetime.utcnow() - timedelta(hours=2),
        opening_cash_egp=500,
    )
    db_session.add(shift)
    await db_session.flush()

    # 8. Reusable Card
    card = ParkingCard(
        garage_id=garage.id,
        card_code="CRD-SRV-101",
        status=CardStatus.AVAILABLE,
    )
    db_session.add(card)
    await db_session.flush()

    return {
        "owner": owner,
        "garage": garage,
        "gates": gates,
        "config": config,
        "pricing_rule": pricing_rule,
        "operator": operator,
        "shift": shift,
        "card": card,
    }


# ===========================================================================
# Task 7.1: Atomic Receipt Counter Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_atomic_receipt_counter_and_tenant_status(db_session: AsyncSession, services_env: dict):
    """Verifies atomic sequential receipt numbers and tenant status enforcement."""
    garage = services_env["garage"]
    counter_service = CounterService(db_session)

    # 1. Sequential numbering
    seq1 = await counter_service.get_next_receipt_number(garage.id)
    seq2 = await counter_service.get_next_receipt_number(garage.id)
    seq3 = await counter_service.get_next_receipt_number(garage.id)

    assert seq2 == seq1 + 1
    assert seq3 == seq2 + 1
    curr = await counter_service.get_current_receipt_number(garage.id)
    assert curr == seq3

    # 2. Non-existent garage
    with pytest.raises(TenantNotFoundError):
        await counter_service.get_next_receipt_number(999999)

    # 3. Suspended garage without grace mode
    garage.status = "SUSPENDED"
    await db_session.flush()

    with pytest.raises(TenantSuspendedError):
        await counter_service.get_next_receipt_number(garage.id, allow_grace=False)

    # Suspended garage with grace mode (e.g. exit settlement)
    grace_seq = await counter_service.get_next_receipt_number(garage.id, allow_grace=True)
    assert grace_seq == seq3 + 1

    # 4. Terminated garage blocks all operations
    garage.status = "TERMINATED"
    await db_session.flush()

    with pytest.raises(TenantTerminatedError):
        await counter_service.get_next_receipt_number(garage.id, allow_grace=True)


# ===========================================================================
# Task 7.2: Platform Config & Entitlements Matrix Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_config_service_effective_entitlements_and_validation(db_session: AsyncSession, services_env: dict):
    """Verifies effective permission calculation (Entitlement and Setting) and validation rules."""
    garage = services_env["garage"]
    config = services_env["config"]
    config_service = ConfigService(db_session)

    # 1. Effective permissions: both garage entitlement and config setting are True
    eff = await config_service.get_effective_config(garage.id)
    assert eff["effective_anpr"] is True
    assert eff["effective_subscriptions"] is True

    # 2. Revoke ANPR from garage: config.anpr_enabled is still True, but effective_anpr MUST become False!
    garage.can_use_anpr = False
    await db_session.flush()

    eff_revoked = await config_service.get_effective_config(garage.id)
    assert eff_revoked["anpr_enabled"] is True
    assert eff_revoked["can_use_anpr"] is False
    assert eff_revoked["effective_anpr"] is False  # Disabled operationally!

    # 3. Attempting to enable an unentitled feature raises FeatureNotEntitledError
    with pytest.raises(FeatureNotEntitledError) as exc_info:
        await config_service.update_config(
            garage_id=garage.id,
            update_data={"anpr_enabled": True},
            expected_version=config.version,
        )
    assert "ANPR feature is not entitled" in str(exc_info.value)

    # 4. Attempting to enable exit_photo_enabled when ANPR is disabled raises ExitPhotoRequiresANPRError
    garage.can_use_anpr = True
    await db_session.flush()

    with pytest.raises(ExitPhotoRequiresANPRError):
        await config_service.update_config(
            garage_id=garage.id,
            update_data={"anpr_enabled": False, "exit_photo_enabled": True},
            expected_version=config.version,
        )


@pytest.mark.asyncio
async def test_config_service_active_session_lock_and_optimistic_concurrency(db_session: AsyncSession, services_env: dict):
    """Verifies locking ticket_system when active sessions exist and optimistic concurrency control."""
    garage = services_env["garage"]
    config = services_env["config"]
    shift = services_env["shift"]
    operator = services_env["operator"]
    session_repo = ParkingSessionRepository(db_session)
    config_service = ConfigService(db_session)

    # 1. Create an active session
    await session_repo.create(
        garage_id=garage.id,
        receipt_no=901,
        ticket_code="TK-ACTIVE-LOCK",
        gate_number=shift.gate_number,
        shift_id=shift.id,
        operator_id=operator.id,
    )
    await db_session.flush()

    active_cnt = await session_repo.count_active(garage.id)
    assert active_cnt >= 1

    # 2. Attempting to change ticket_system raises ConfigLockedActiveSessionsError
    with pytest.raises(ConfigLockedActiveSessionsError) as exc_info:
        await config_service.update_config(
            garage_id=garage.id,
            update_data={"ticket_system": "PAPER_BARCODE_TICKETS"},
            expected_version=config.version,
        )
    assert "Cannot change ticket system" in str(exc_info.value)

    # 3. Optimistic concurrency check with mismatched version
    with pytest.raises(ConfigVersionConflictError):
        await config_service.update_config(
            garage_id=garage.id,
            update_data={"min_session_duration_seconds": 90},
            expected_version=999,  # Wrong version
        )

    # 4. Successful update increments version
    current_ver = config.version
    updated = await config_service.update_config(
        garage_id=garage.id,
        update_data={"min_session_duration_seconds": 90},
        expected_version=current_ver,
    )
    assert updated.version == current_ver + 1
    assert updated.min_session_duration_seconds == 90


# ===========================================================================
# Task 7.3: Session Service Tests (Grace Mode, Locks, Paper Tickets, Short Sessions)
# ===========================================================================

@pytest.mark.asyncio
async def test_exit_only_grace_mode_suspended_and_past_due(db_session: AsyncSession, services_env: dict):
    """Verifies Exit-Only grace mode: entries blocked under SUSPENDED/PAST_DUE, exits permitted."""
    garage = services_env["garage"]
    owner = services_env["owner"]
    operator = services_env["operator"]
    card = services_env["card"]
    shift = services_env["shift"]

    card_service = CardService(db_session)
    session_repo = ParkingSessionRepository(db_session)
    pricing_service = PricingService(db_session)
    shift_service = ShiftService(db_session, AuditService(db_session))
    audit_service = AuditService(db_session)
    plate_service = PlateService()

    session_service = SessionService(
        db=db_session,
        card_service=card_service,
        session_repo=session_repo,
        pricing_service=pricing_service,
        shift_service=shift_service,
        audit_service=audit_service,
        plate_service=plate_service,
    )

    # 1. Open session while ACTIVE
    res = await session_service.open_session(
        card_code=card.card_code,
        operator_id=operator.id,
    )
    session_id = res.session.id

    # 2. Suspend garage: entry blocked, exit permitted
    garage.status = "SUSPENDED"
    await db_session.flush()

    # Second card
    card2 = ParkingCard(
        garage_id=garage.id,
        card_code="CRD-SRV-102",
        status=CardStatus.AVAILABLE,
    )
    db_session.add(card2)
    await db_session.flush()

    with pytest.raises(TenantSuspendedError):
        await session_service.open_session(card_code=card2.card_code, operator_id=operator.id)

    # Exit of existing session MUST succeed!
    closed_sess, calc = await session_service.close_session(
        session_id=session_id,
        exit_operator_id=operator.id,
        confirm_short_session=True,
    )
    assert closed_sess.status == SessionStatus.COMPLETED
    assert closed_sess.is_paid is True

    # 3. Test Owner PAST_DUE status
    garage.status = "ACTIVE"
    owner.billing_status = "PAST_DUE"
    await db_session.flush()

    # Opening session under PAST_DUE raises BillingPastDueError
    with pytest.raises(BillingPastDueError):
        await session_service.open_session(card_code=card2.card_code, operator_id=operator.id)

    # 4. Terminated garage blocks both entry and exit
    garage.status = "TERMINATED"
    await db_session.flush()

    with pytest.raises(TenantTerminatedError):
        await session_service.open_session(card_code=card2.card_code, operator_id=operator.id)


@pytest.mark.asyncio
async def test_two_int_advisory_lock_rapid_double_scan(db_session: AsyncSession, services_env: dict):
    """Verifies PostgreSQL two-int transaction-level advisory locking prevents rapid double scan."""
    garage = services_env["garage"]
    card = services_env["card"]

    card_service = CardService(db_session)
    session_repo = ParkingSessionRepository(db_session)
    pricing_service = PricingService(db_session)
    shift_service = ShiftService(db_session, AuditService(db_session))
    audit_service = AuditService(db_session)
    plate_service = PlateService()

    session_service = SessionService(
        db=db_session,
        card_service=card_service,
        session_repo=session_repo,
        pricing_service=pricing_service,
        shift_service=shift_service,
        audit_service=audit_service,
        plate_service=plate_service,
    )

    # Directly test advisory lock on Postgres
    settings = get_settings()
    engine2 = create_async_engine(settings.MIGRATION_DATABASE_URL or settings.DATABASE_URL, poolclass=NullPool)
    async with engine2.connect() as conn2:
        trans2 = await conn2.begin()
        try:
            # Connection 2 acquires the lock on (garage.id, card.id)
            res2 = await conn2.execute(
                text("SELECT pg_try_advisory_xact_lock(:gid, :cid)"),
                {"gid": garage.id, "cid": card.id},
            )
            locked2 = res2.scalar()
            assert locked2 is True

            # Connection 1 (inside db_session) tries to acquire the same lock -> fails and raises RapidDoubleScanError!
            with pytest.raises(RapidDoubleScanError):
                await session_service._acquire_advisory_lock(garage.id, card.id)
        finally:
            await trans2.rollback()
    await engine2.dispose()


@pytest.mark.asyncio
async def test_paper_barcode_ticket_lifecycle(db_session: AsyncSession, services_env: dict):
    """Verifies paper ticket generation (random non-sequential code) and barcode exit resolution."""
    garage = services_env["garage"]
    operator = services_env["operator"]

    card_service = CardService(db_session)
    session_repo = ParkingSessionRepository(db_session)
    pricing_service = PricingService(db_session)
    shift_service = ShiftService(db_session, AuditService(db_session))
    audit_service = AuditService(db_session)
    plate_service = PlateService()

    session_service = SessionService(
        db=db_session,
        card_service=card_service,
        session_repo=session_repo,
        pricing_service=pricing_service,
        shift_service=shift_service,
        audit_service=audit_service,
        plate_service=plate_service,
    )

    # 1. Open paper ticket session
    res = await session_service.open_paper_ticket_session(
        operator_id=operator.id,
        plate_number="Cairo 123 ABC",
    )
    session = res.session
    assert session.ticket_code is not None
    assert session.ticket_code.startswith("TK-")
    assert session.card_id is None
    assert session.card_code is None
    assert session.receipt_no > 0
    assert session.status == SessionStatus.ACTIVE

    # 2. Scanning ticket barcode triggers exit
    closed_sess, calc = await session_service.close_session_by_code(
        code=session.ticket_code,
        exit_operator_id=operator.id,
        confirm_short_session=True,
    )
    assert closed_sess.id == session.id
    assert closed_sess.status == SessionStatus.COMPLETED
    assert closed_sess.is_paid is True
    assert calc is not None


@pytest.mark.asyncio
async def test_short_session_confirmation_guard(db_session: AsyncSession, services_env: dict):
    """Verifies short session detection requires explicit confirm_short_session=True."""
    operator = services_env["operator"]
    card = services_env["card"]

    card_service = CardService(db_session)
    session_repo = ParkingSessionRepository(db_session)
    pricing_service = PricingService(db_session)
    shift_service = ShiftService(db_session, AuditService(db_session))
    audit_service = AuditService(db_session)
    plate_service = PlateService()

    session_service = SessionService(
        db=db_session,
        card_service=card_service,
        session_repo=session_repo,
        pricing_service=pricing_service,
        shift_service=shift_service,
        audit_service=audit_service,
        plate_service=plate_service,
    )

    # 1. Open session (starts right now)
    res = await session_service.open_session(card_code=card.card_code, operator_id=operator.id)
    session_id = res.session.id

    # 2. Attempt closing immediately without confirmation -> raises ShortSessionConfirmationRequiredError
    with pytest.raises(ShortSessionConfirmationRequiredError) as exc_info:
        await session_service.close_session(
            session_id=session_id,
            exit_operator_id=operator.id,
            confirm_short_session=False,
        )
    assert exc_info.value.min_seconds == 60
    assert exc_info.value.elapsed_seconds < 60

    # 3. Passing confirm_short_session=True successfully closes the session
    closed_sess, calc = await session_service.close_session(
        session_id=session_id,
        exit_operator_id=operator.id,
        confirm_short_session=True,
    )
    assert closed_sess.status == SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_lost_card_and_penalty_resolution(db_session: AsyncSession, services_env: dict):
    """Verifies lost card flow applies penalty and marks card & session accordingly."""
    operator = services_env["operator"]
    card = services_env["card"]
    pricing_rule = services_env["pricing_rule"]

    card_service = CardService(db_session)
    session_repo = ParkingSessionRepository(db_session)
    pricing_service = PricingService(db_session)
    shift_service = ShiftService(db_session, AuditService(db_session))
    audit_service = AuditService(db_session)
    plate_service = PlateService()

    session_service = SessionService(
        db=db_session,
        card_service=card_service,
        session_repo=session_repo,
        pricing_service=pricing_service,
        shift_service=shift_service,
        audit_service=audit_service,
        plate_service=plate_service,
    )

    res = await session_service.open_session(card_code=card.card_code, operator_id=operator.id)
    session_id = res.session.id

    # Resolve lost card
    lost_sess, calc = await session_service.resolve_lost_card(
        session_id=session_id,
        operator_id=operator.id,
        notes="Customer lost card in parking area",
    )

    assert lost_sess.status == SessionStatus.LOST_CARD
    assert lost_sess.is_lost_card is True
    assert lost_sess.lost_card_penalty_applied == pricing_rule.lost_card_penalty
    assert calc.penalty_amount == pricing_rule.lost_card_penalty

    # Verify card status was updated to LOST
    card_in_db = await card_service.get_by_code(card.card_code)
    assert card_in_db.status == CardStatus.LOST


# ===========================================================================
# Task 7.4: Dynamic Gate Reporting Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_dynamic_gate_reporting_all_gates(db_session: AsyncSession, services_env: dict):
    """Verifies report service returns all 8 gates dynamically from garage_gates."""
    garage = services_env["garage"]
    gates = services_env["gates"]
    assert len(gates) == 8

    report_service = ReportService(db_session)
    panel = await report_service.get_gate_panel(garage_id=garage.id)

    # Exactly 8 gate panels must be returned
    assert len(panel) == 8
    gate_numbers = [p.gate_number for p in panel]
    assert gate_numbers == [1, 2, 3, 4, 5, 6, 7, 8]

    # Gate 1 has the active shift from services_env
    g1 = panel[0]
    assert g1.gate_number == 1
    assert g1.operator_name == "Service Operator One"


# ===========================================================================
# Task 7.5: System Context & Automated Audit Logging Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_system_context_audit_logging(db_session: AsyncSession, services_env: dict):
    """Verifies system audit logging with actor_id=None does not violate ck_audit_garage_context."""
    audit_service = AuditService(db_session)

    # 1. Automated platform event without actor or garage
    await audit_service.log(
        actor_id=None,
        action="AUTOMATED_MAINTENANCE_RUN",
        entity_type="platform",
        entity_id=999,
        actor_type="system",
        garage_id=None,
        after={"status": "CLEANUP_SUCCESS"},
    )
    await db_session.flush()

    # Query to verify it was written
    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "AUTOMATED_MAINTENANCE_RUN",
            AuditLog.actor_type == "system",
        ).limit(1)
    )
    log_entry = result.scalars().first()
    assert log_entry is not None
    assert log_entry.actor_id is None
    assert log_entry.garage_id is None
    assert log_entry.entity_type == "platform"
