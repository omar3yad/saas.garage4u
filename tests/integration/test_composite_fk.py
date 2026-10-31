import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.owner import Owner
from models.garage import Garage
from models.garage_gate import GarageGate
from models.shift import Shift
from models.parking_card import ParkingCard, CardStatus
from models.parking_session import ParkingSession, SessionStatus
from models.user import User, UserRole
from services.auth_service import AuthService
from config import get_settings


@pytest.mark.asyncio
async def test_composite_foreign_key_cross_tenant_rejection(pg_migrator_session: AsyncSession):
    """Verifies that composite foreign keys prevent cross-tenant shift and card assignments with IntegrityError."""
    session = pg_migrator_session
    auth_svc = AuthService(get_settings())

    # Create Owner & Two Garages
    owner = Owner(
        name="Composite FK Test Owner",
        company_name="FK Corp",
        contact_phone="+201009998888",
        contact_email="fk_test@tenant.test",
        billing_status="ACTIVE",
        max_garages_allowed=2,
    )
    session.add(owner)
    await session.flush()

    garage_a = Garage(
        owner_id=owner.id,
        name="Garage FK-A",
        code="GAR-FKA",
        status="ACTIVE",
        capacity=50,
        can_use_anpr=True,
        can_use_subscriptions=True,
        max_gates=2,
    )
    garage_b = Garage(
        owner_id=owner.id,
        name="Garage FK-B",
        code="GAR-FKB",
        status="ACTIVE",
        capacity=50,
        can_use_anpr=True,
        can_use_subscriptions=True,
        max_gates=2,
    )
    session.add_all([garage_a, garage_b])
    await session.flush()

    # Create Gates
    gate_a = GarageGate(garage_id=garage_a.id, gate_number=1, name="Gate A1", gate_type="ENTRY")
    gate_b = GarageGate(garage_id=garage_b.id, gate_number=1, name="Gate B1", gate_type="ENTRY")
    session.add_all([gate_a, gate_b])
    await session.flush()

    # Create Operators
    op_a = User(
        username="op_fk_a",
        full_name="Operator FK A",
        role=UserRole.OPERATOR,
        owner_id=owner.id,
        garage_id=garage_a.id,
        gate_number=1,
        hashed_password=auth_svc.hash_password("Pass123!"),
        is_active=True,
    )
    op_b = User(
        username="op_fk_b",
        full_name="Operator FK B",
        role=UserRole.OPERATOR,
        owner_id=owner.id,
        garage_id=garage_b.id,
        gate_number=1,
        hashed_password=auth_svc.hash_password("Pass123!"),
        is_active=True,
    )
    session.add_all([op_a, op_b])
    await session.flush()

    # Shift for Garage A and Shift for Garage B
    shift_a = Shift(
        garage_id=garage_a.id,
        operator_id=op_a.id,
        gate_number=1,
        started_at=(datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None),
    )
    shift_b = Shift(
        garage_id=garage_b.id,
        operator_id=op_b.id,
        gate_number=1,
        started_at=(datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None),
    )
    # Cards for Garage A and Card for Garage B
    card_a = ParkingCard(garage_id=garage_a.id, card_code="CRD-FKA-01", status=CardStatus.AVAILABLE)
    card_a2 = ParkingCard(garage_id=garage_a.id, card_code="CRD-FKA-02", status=CardStatus.AVAILABLE)
    card_b = ParkingCard(garage_id=garage_b.id, card_code="CRD-FKB-01", status=CardStatus.AVAILABLE)
    session.add_all([shift_a, shift_b, card_a, card_a2, card_b])
    await session.flush()

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Valid Session in Garage A referencing shift_a and card_a -> MUST SUCCEED
    valid_session = ParkingSession(
        garage_id=garage_a.id,
        card_id=card_a.id,
        ticket_code="TKT-VALID-01",
        receipt_no=1001,
        shift_id=shift_a.id,
        operator_id=op_a.id,
        entry_time=now,
        gate_number=1,
        status=SessionStatus.ACTIVE,
    )
    session.add(valid_session)
    await session.flush()

    # 2. Rogue Session in Garage A referencing shift_b (from Garage B) -> MUST FAIL WITH IntegrityError
    async with session.begin_nested():
        rogue_shift_session = ParkingSession(
            garage_id=garage_a.id,
            card_id=card_a2.id,
            ticket_code="TKT-ROGUE-01",
            receipt_no=1002,
            shift_id=shift_b.id,  # Cross-tenant shift!
            operator_id=op_a.id,
            entry_time=now,
            gate_number=1,
            status=SessionStatus.ACTIVE,
        )
        session.add(rogue_shift_session)
        with pytest.raises(IntegrityError) as exc_info:
            await session.flush()

        # Confirm composite foreign key violation
        assert "fk_sessions_garage_shift" in str(exc_info.value).lower() or "foreignkeyviolation" in str(exc_info.value).lower() or "foreign key" in str(exc_info.value).lower()

    # 3. Rogue Session in Garage A referencing card_b (from Garage B) -> MUST FAIL WITH IntegrityError
    async with session.begin_nested():
        rogue_card_session = ParkingSession(
            garage_id=garage_a.id,
            card_id=card_b.id,  # Cross-tenant card!
            ticket_code="TKT-ROGUE-02",
            receipt_no=1003,
            shift_id=shift_a.id,
            operator_id=op_a.id,
            entry_time=now,
            gate_number=1,
            status=SessionStatus.ACTIVE,
        )
        session.add(rogue_card_session)
        with pytest.raises(IntegrityError) as exc_info:
            await session.flush()

        assert "fk_sessions_garage_card" in str(exc_info.value).lower() or "foreignkeyviolation" in str(exc_info.value).lower() or "foreign key" in str(exc_info.value).lower()

    # Clean up test rows
    await session.rollback()
