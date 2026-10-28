import pytest
from datetime import datetime
from types import SimpleNamespace

from models.parking_session import SessionStatus, PaymentMethod
from utils.csv_export import (
    _piastres_to_egp_str,
    generate_sessions_csv,
    generate_shifts_csv,
)


def test_piastres_to_egp_str():
    assert _piastres_to_egp_str(None) == ""
    assert _piastres_to_egp_str(0) == "0.00"
    assert _piastres_to_egp_str(2550) == "25.50"
    assert _piastres_to_egp_str(100) == "1.00"


async def async_iter(items):
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_csv_starts_with_bom():
    session = SimpleNamespace(
        id=1,
        card_code="CARD1",
        plate_number="ABC",
        gate_number=1,
        operator_id=1,
        entry_time=datetime(2024, 8, 15, 10, 0),
        exit_time=datetime(2024, 8, 15, 12, 0),
        duration_minutes=120,
        status=SessionStatus.COMPLETED,
        amount_charged=2000,
        is_lost_card=False,
        payment_method=PaymentMethod.CASH,
    )
    generator = generate_sessions_csv(async_iter([session]), {1: "Operator 1"})
    chunks = [chunk async for chunk in generator]

    assert len(chunks) >= 3
    assert chunks[0] == "\ufeff"


@pytest.mark.asyncio
async def test_csv_header_row_is_arabic():
    session = SimpleNamespace(
        id=1,
        card_code="CARD1",
        plate_number="ABC",
        gate_number=1,
        operator_id=1,
        entry_time=datetime(2024, 8, 15, 10, 0),
        exit_time=None,
        duration_minutes=None,
        status=SessionStatus.ACTIVE,
        amount_charged=None,
        is_lost_card=False,
        payment_method=PaymentMethod.CASH,
    )
    generator = generate_sessions_csv(async_iter([session]), {})
    chunks = [chunk async for chunk in generator]

    # Chunk 1 is header
    assert "رقم الجلسة" in chunks[1]
    assert "رمز الكرت" in chunks[1]
    assert "رقم اللوحة" in chunks[1]


@pytest.mark.asyncio
async def test_none_values_render_as_empty_string():
    session = SimpleNamespace(
        id=1,
        card_code="CARD-NULL-TEST",
        plate_number=None,
        gate_number=1,
        operator_id=None,
        entry_time=None,
        exit_time=None,
        duration_minutes=None,
        status=SessionStatus.ACTIVE,
        amount_charged=None,
        is_lost_card=False,
        payment_method=None,
    )
    generator = generate_sessions_csv(async_iter([session]), {})
    chunks = [chunk async for chunk in generator]

    data_row = chunks[2]
    assert "CARD-NULL-TEST" in data_row
    assert "None" not in data_row
    assert "null" not in data_row


@pytest.mark.asyncio
async def test_shifts_csv_export():
    shift = SimpleNamespace(
        id=10,
        operator_id=2,
        gate_number=1,
        started_at=datetime(2024, 8, 15, 8, 0),
        ended_at=datetime(2024, 8, 15, 16, 0),
        session_count=5,
        closing_cash_egp=5000,
    )
    generator = generate_shifts_csv(
        async_iter([shift]),
        operator_names={2: "Ahmed Operator"},
        session_totals={10: 4500},
    )
    chunks = [chunk async for chunk in generator]
    assert len(chunks) == 3
    assert chunks[0] == "\ufeff"
    assert "رقم الشيفت" in chunks[1]
    assert "الإجمالي المحسوب (جنيه)" in chunks[1]
    # Data row
    assert "Ahmed Operator" in chunks[2]
    assert "45.00" in chunks[2]
    assert "50.00" in chunks[2]
    assert "5.00" in chunks[2]
