import pytest
from pydantic import ValidationError
from datetime import datetime
from models.user import UserRole
from schemas.user import UserCreate, UserUpdate
from schemas.owner import OwnerCreate, OwnerUpdate
from schemas.garage import GarageCreate, GarageEntitlementsUpdate
from schemas.garage_config import GarageConfigBase, GarageConfigUpdate
from schemas.garage_gate import GarageGateCreate, GarageGateUpdate
from schemas.edge_device import EdgeDeviceCreate, EdgeDeviceGateAssign
from schemas.admin_reports import ReportFilters, ShiftFilters
from schemas.parking_session import SessionResponse
from schemas.shift import ShiftResponse


def test_user_schema_gate_number_validation():
    # Accept gate_number=8 for operator
    user = UserCreate(
        full_name="Operator 8",
        username="op8",
        password="password123",
        role=UserRole.OPERATOR,
        gate_number=8,
        garage_id=1,
        owner_id=1,
    )
    assert user.gate_number == 8

    # Reject gate_number=11 (> 10)
    with pytest.raises(ValidationError):
        UserCreate(
            full_name="Operator 11",
            username="op11",
            password="password123",
            role=UserRole.OPERATOR,
            gate_number=11,
            garage_id=1,
            owner_id=1,
        )

    # Reject operator without gate_number
    with pytest.raises(ValidationError, match="Gate number is required for operators"):
        UserCreate(
            full_name="Operator None",
            username="op_none",
            password="password123",
            role=UserRole.OPERATOR,
            gate_number=None,
        )

    # Reject manager with gate_number
    with pytest.raises(ValidationError, match="Gate number must be null for MANAGER"):
        UserCreate(
            full_name="Manager 1",
            username="mgr1",
            password="password123",
            role=UserRole.MANAGER,
            gate_number=1,
        )


def test_owner_schema_validation():
    # Valid owner
    owner = OwnerCreate(
        name="Valid Owner",
        contact_phone="01012345678",
        contact_email="owner@test.com",
        max_garages_allowed=2,
    )
    assert owner.name == "Valid Owner"
    assert owner.max_garages_allowed == 2

    # Reject invalid email
    with pytest.raises(ValidationError):
        OwnerCreate(
            name="Owner Bad Email",
            contact_phone="01012345678",
            contact_email="not-an-email",
        )

    # Reject short phone
    with pytest.raises(ValidationError):
        OwnerCreate(
            name="Owner Short Phone",
            contact_phone="123",
            contact_email="owner@test.com",
        )

    # Reject max_garages_allowed < 1
    with pytest.raises(ValidationError):
        OwnerCreate(
            name="Owner Zero Garages",
            contact_phone="01012345678",
            contact_email="owner@test.com",
            max_garages_allowed=0,
        )


def test_garage_schema_validation():
    # Valid garage
    garage = GarageCreate(
        owner_id=1,
        code="CAI-01",
        name="Cairo Central Garage",
        capacity=200,
        status="ACTIVE",
        max_gates=5,
    )
    assert garage.code == "CAI-01"
    assert garage.max_gates == 5

    # Reject lowercase or invalid code
    with pytest.raises(ValidationError):
        GarageCreate(
            owner_id=1,
            code="cai-01!",
            name="Garage Bad Code",
            capacity=100,
        )

    # Reject non-positive capacity
    with pytest.raises(ValidationError):
        GarageCreate(
            owner_id=1,
            code="CAI-02",
            name="Zero Cap",
            capacity=0,
        )

    # Reject max_gates > 10
    with pytest.raises(ValidationError):
        GarageCreate(
            owner_id=1,
            code="CAI-03",
            name="Too Many Gates",
            capacity=100,
            max_gates=15,
        )

    # Reject invalid status
    with pytest.raises(ValidationError):
        GarageCreate(
            owner_id=1,
            code="CAI-04",
            name="Bad Status",
            capacity=100,
            status="DELETED",
        )

    # Entitlements update validation
    entitlements = GarageEntitlementsUpdate(can_use_anpr=True, max_gates=10)
    assert entitlements.can_use_anpr is True
    assert entitlements.max_gates == 10

    with pytest.raises(ValidationError):
        GarageEntitlementsUpdate(status="INVALID")


def test_garage_config_schema_validation():
    # Valid config
    config = GarageConfigBase(
        ticket_system="REUSABLE_CARDS",
        anpr_enabled=True,
        exit_photo_enabled=True,
        min_session_duration_seconds=120,
    )
    assert config.exit_photo_enabled is True

    # Reject exit_photo_enabled=True when anpr_enabled=False
    with pytest.raises(ValidationError, match="exit_photo_enabled requires anpr_enabled to be True"):
        GarageConfigBase(
            anpr_enabled=False,
            exit_photo_enabled=True,
        )

    # Reject min_session_duration_seconds < 30 or > 600
    with pytest.raises(ValidationError):
        GarageConfigBase(min_session_duration_seconds=20)

    with pytest.raises(ValidationError):
        GarageConfigBase(min_session_duration_seconds=700)

    # Update schema consistency check
    with pytest.raises(ValidationError, match="exit_photo_enabled requires anpr_enabled to be True"):
        GarageConfigUpdate(anpr_enabled=False, exit_photo_enabled=True)


def test_garage_gate_schema_validation():
    # Valid gate
    gate = GarageGateCreate(
        gate_number=5,
        name="Gate 5",
        paper_width_mm=80,
    )
    assert gate.gate_number == 5
    assert gate.paper_width_mm == 80

    # Accept 58mm
    gate58 = GarageGateCreate(
        gate_number=2,
        name="Gate 2",
        paper_width_mm=58,
    )
    assert gate58.paper_width_mm == 58

    # Reject unsupported paper width (e.g. 70mm)
    with pytest.raises(ValidationError, match="paper_width_mm must be 58 or 80"):
        GarageGateCreate(
            gate_number=1,
            name="Gate 1",
            paper_width_mm=70,
        )

    # Reject gate_number > 10
    with pytest.raises(ValidationError):
        GarageGateCreate(
            gate_number=12,
            name="Gate 12",
        )


def test_edge_device_schema_validation():
    # Valid gate assignment
    assign = EdgeDeviceGateAssign(gate_number=4, direction="BIDIRECTIONAL")
    assert assign.direction == "BIDIRECTIONAL"

    assign_entry = EdgeDeviceGateAssign(gate_number=1, direction="ENTRY")
    assert assign_entry.direction == "ENTRY"

    # Reject invalid direction
    with pytest.raises(ValidationError):
        EdgeDeviceGateAssign(gate_number=1, direction="SIDEWAYS")

    # Reject gate_number > 10
    with pytest.raises(ValidationError):
        EdgeDeviceGateAssign(gate_number=15)


def test_admin_reports_filters_gate_limit():
    # Accept gate_number=7 and gate_number=10
    rf = ReportFilters(gate_number=7)
    assert rf.gate_number == 7

    sf = ShiftFilters(gate_number=10)
    assert sf.gate_number == 10

    # Reject gate_number=11
    with pytest.raises(ValidationError):
        ReportFilters(gate_number=11)

    with pytest.raises(ValidationError):
        ShiftFilters(gate_number=11)


def test_session_and_shift_response_fields():
    now = datetime.now()
    session = SessionResponse(
        id=1,
        garage_id=1,
        receipt_no=42,
        ticket_code="TICKET-123",
        card_id=None,
        card_code=None,
        status="ACTIVE",
        gate_number=2,
        shift_id=1,
        operator_id=1,
        entry_time=now,
        exit_time=None,
        plate_number="123-ABC",
        duration_minutes=None,
        pricing_rule_id=None,
        amount_charged=None,
        is_lost_card=False,
        lost_card_penalty_applied=None,
        payment_method="CASH",
        is_paid=False,
        exit_operator_id=None,
        exit_shift_id=None,
        receipt_printed_at=None,
        notes=None,
        created_at=now,
    )
    assert session.garage_id == 1
    assert session.receipt_no == 42
    assert session.ticket_code == "TICKET-123"
    assert session.card_id is None

    shift = ShiftResponse(
        id=1,
        garage_id=1,
        operator_id=1,
        gate_number=3,
        started_at=now,
        ended_at=None,
        opening_cash_egp=500,
        closing_cash_egp=None,
        notes=None,
        created_at=now,
        updated_at=now,
    )
    assert shift.garage_id == 1
    assert shift.gate_number == 3
