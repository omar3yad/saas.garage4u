import pytest
from datetime import datetime, timedelta
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User, UserRole
from models.shift import Shift
from models.parking_session import ParkingSession, SessionStatus
from models.parking_card import ParkingCard, CardStatus
from models.pricing_rule import PricingRule
from services.auth_service import AuthService


async def setup_admin(
    db: AsyncSession, auth_service: AuthService, client: AsyncClient
) -> User:
    admin = User(
        username="admin_ui_test",
        full_name="Admin UI Tester",
        role=UserRole.ADMIN,
        is_active=True,
        hashed_password=auth_service.hash_password("adminpass123"),
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)

    token = auth_service.create_access_token(admin.id, admin.role.value)
    client.cookies.set("pgms_token", token)
    return admin


async def setup_operator(
    db: AsyncSession, auth_service: AuthService, client: AsyncClient
) -> User:
    op = User(
        username="operator_ui_test",
        full_name="Operator UI Tester",
        role=UserRole.OPERATOR,
        gate_number=1,
        is_active=True,
        hashed_password=auth_service.hash_password("oppass123"),
    )
    db.add(op)
    await db.commit()
    await db.refresh(op)

    token = auth_service.create_access_token(op.id, op.role.value)
    client.cookies.set("pgms_token", token)
    return op


@pytest.mark.asyncio
async def test_dashboard_requires_admin(
    async_client: AsyncClient, db_session: AsyncSession, auth_service: AuthService
):
    # GET as operator -> 303 redirect to /ui/login?next=/ui/admin/dashboard
    await setup_operator(db_session, auth_service, async_client)
    res = await async_client.get("/ui/admin/dashboard", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/ui/login?next=/ui/admin/dashboard"


@pytest.mark.asyncio
async def test_dashboard_renders_as_admin(
    async_client: AsyncClient, db_session: AsyncSession, auth_service: AuthService
):
    await setup_admin(db_session, auth_service, async_client)
    res = await async_client.get("/ui/admin/dashboard")
    assert res.status_code == 200
    assert "لوحة التحكم" in res.text


@pytest.mark.asyncio
async def test_shifts_page_renders(
    async_client: AsyncClient, db_session: AsyncSession, auth_service: AuthService
):
    await setup_admin(db_session, auth_service, async_client)
    res = await async_client.get("/ui/admin/shifts")
    assert res.status_code == 200
    assert "الشيفتات" in res.text


@pytest.mark.asyncio
async def test_sessions_page_renders(
    async_client: AsyncClient, db_session: AsyncSession, auth_service: AuthService
):
    await setup_admin(db_session, auth_service, async_client)
    res = await async_client.get("/ui/admin/sessions")
    assert res.status_code == 200
    assert "دخول وخروج" in res.text


@pytest.mark.asyncio
async def test_revenue_report_renders(
    async_client: AsyncClient, db_session: AsyncSession, auth_service: AuthService
):
    await setup_admin(db_session, auth_service, async_client)
    res = await async_client.get("/ui/admin/reports/revenue")
    assert res.status_code == 200
    assert "تقرير الإيراد" in res.text


@pytest.mark.asyncio
async def test_print_view_shift_missing_shift_id(
    async_client: AsyncClient, db_session: AsyncSession, auth_service: AuthService
):
    await setup_admin(db_session, auth_service, async_client)
    res = await async_client.get("/ui/admin/reports/print?report_type=shift")
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_print_view_invalid_report_type(
    async_client: AsyncClient, db_session: AsyncSession, auth_service: AuthService
):
    await setup_admin(db_session, auth_service, async_client)
    res = await async_client.get("/ui/admin/reports/print?report_type=unknown")
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_print_view_sessions_renders(
    async_client: AsyncClient, db_session: AsyncSession, auth_service: AuthService
):
    admin = await setup_admin(db_session, auth_service, async_client)

    card1 = ParkingCard(card_code="PRINT_SESS_1", status=CardStatus.IN_USE)
    card2 = ParkingCard(card_code="PRINT_SESS_2", status=CardStatus.IN_USE)
    db_session.add_all([card1, card2])
    await db_session.flush()

    s1 = ParkingSession(
        card_id=card1.id,
        card_code=card1.card_code,
        gate_number=1,
        shift_id=1,
        operator_id=admin.id,
        entry_time=datetime.utcnow() - timedelta(hours=2),
        status=SessionStatus.ACTIVE,
    )
    s2 = ParkingSession(
        card_id=card2.id,
        card_code=card2.card_code,
        gate_number=2,
        shift_id=1,
        operator_id=admin.id,
        entry_time=datetime.utcnow() - timedelta(hours=1),
        status=SessionStatus.ACTIVE,
    )
    db_session.add_all([s1, s2])
    await db_session.commit()

    res = await async_client.get("/ui/admin/reports/print?report_type=sessions")
    assert res.status_code == 200
    assert "window.print()" in res.text
    assert "PRINT_SESS_1" in res.text
    assert "PRINT_SESS_2" in res.text


@pytest.mark.asyncio
async def test_print_view_shift_renders(
    async_client: AsyncClient, db_session: AsyncSession, auth_service: AuthService
):
    admin = await setup_admin(db_session, auth_service, async_client)

    shift = Shift(
        operator_id=admin.id,
        gate_number=1,
        started_at=datetime.utcnow() - timedelta(hours=4),
        opening_cash_egp=5000,
    )
    db_session.add(shift)
    await db_session.commit()
    await db_session.refresh(shift)

    res = await async_client.get(
        f"/ui/admin/reports/print?report_type=shift&shift_id={shift.id}"
    )
    assert res.status_code == 200
    assert "window.print()" in res.text
    assert f"#{shift.id}" in res.text or "تقرير إغلاق الشيفت" in res.text


@pytest.mark.asyncio
async def test_rates_page_renders(
    async_client: AsyncClient, db_session: AsyncSession, auth_service: AuthService
):
    await setup_admin(db_session, auth_service, async_client)

    rule = PricingRule(
        label="Test Pricing Rate",
        rate_per_hour=1000,
        minimum_charge=500,
        grace_period_mins=15,
        lost_card_penalty=2000,
        is_active=True,
        created_by=1,
        effective_from=datetime.utcnow(),
    )
    db_session.add(rule)
    await db_session.commit()

    res = await async_client.get("/ui/admin/rates")
    assert res.status_code == 200
    assert "التعريفات السعرية" in res.text
    assert "Test Pricing Rate" in res.text


@pytest.mark.asyncio
async def test_operators_page_renders(
    async_client: AsyncClient, db_session: AsyncSession, auth_service: AuthService
):
    await setup_admin(db_session, auth_service, async_client)
    res = await async_client.get("/ui/admin/operators")
    assert res.status_code == 200
    assert "العمال" in res.text


@pytest.mark.asyncio
async def test_dashboard_long_stay_banner_shown(
    async_client: AsyncClient, db_session: AsyncSession, auth_service: AuthService
):
    admin = await setup_admin(db_session, auth_service, async_client)

    card = ParkingCard(card_code="LONG_STAY_CARD", status=CardStatus.IN_USE)
    db_session.add(card)
    await db_session.flush()

    # 25 hours ago -> triggers long stay (>24h)
    old_session = ParkingSession(
        card_id=card.id,
        card_code=card.card_code,
        gate_number=1,
        shift_id=1,
        operator_id=admin.id,
        entry_time=datetime.utcnow() - timedelta(hours=25),
        status=SessionStatus.ACTIVE,
    )
    db_session.add(old_session)
    await db_session.commit()

    res = await async_client.get("/ui/admin/dashboard")
    assert res.status_code == 200
    assert "جلسات مفتوحة أكثر من ٢٤ ساعة" in res.text


@pytest.mark.asyncio
async def test_dashboard_no_banner_when_no_long_stay(
    async_client: AsyncClient, db_session: AsyncSession, auth_service: AuthService
):
    await setup_admin(db_session, auth_service, async_client)

    # Make sure no active session > 24 hours exists
    res = await async_client.get("/ui/admin/dashboard")
    assert res.status_code == 200
    assert "جلسات مفتوحة أكثر من ٢٤ ساعة" not in res.text


@pytest.mark.asyncio
async def test_admin_shift_detail_shows_exit_sessions(
    async_client: AsyncClient, db_session: AsyncSession, auth_service: AuthService
):
    admin = await setup_admin(db_session, auth_service, async_client)

    # Shift 1 (Entry gate)
    entry_shift = Shift(
        operator_id=admin.id,
        gate_number=1,
        started_at=datetime.utcnow() - timedelta(hours=3),
        ended_at=datetime.utcnow() - timedelta(hours=2),
    )
    # Shift 2 (Exit gate)
    exit_shift = Shift(
        operator_id=admin.id,
        gate_number=2,
        started_at=datetime.utcnow() - timedelta(hours=2),
        ended_at=None,
    )
    db_session.add_all([entry_shift, exit_shift])
    await db_session.flush()

    card = ParkingCard(card_code="EXIT-CARD-999", status=CardStatus.AVAILABLE)
    db_session.add(card)
    await db_session.flush()

    # Session entered in shift 1 (entry_shift), but exited in shift 2 (exit_shift)
    session = ParkingSession(
        card_id=card.id,
        card_code=card.card_code,
        gate_number=1,
        shift_id=entry_shift.id,
        operator_id=admin.id,
        exit_shift_id=exit_shift.id,
        exit_operator_id=admin.id,
        entry_time=datetime.utcnow() - timedelta(hours=2, minutes=30),
        exit_time=datetime.utcnow() - timedelta(minutes=10),
        amount_charged=3500,
        status=SessionStatus.COMPLETED,
    )
    db_session.add(session)
    await db_session.commit()

    # 1. Check UI Admin Shift Detail for exit_shift
    res = await async_client.get(f"/ui/admin/shifts/{exit_shift.id}")
    assert res.status_code == 200
    assert "EXIT-CARD-999" in res.text
    assert "لا توجد حركات مسجلة لهذا الشيفت" not in res.text

    # 2. Check CSV export for exit_shift
    csv_res = await async_client.get(f"/api/v1/admin/shifts/{exit_shift.id}/export/csv")
    assert csv_res.status_code == 200
    assert "EXIT-CARD-999" in csv_res.text

