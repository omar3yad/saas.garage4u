from datetime import date, datetime, timedelta
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    User,
    UserRole,
    ParkingCard,
    CardStatus,
    SubscriptionPlan,
    Subscriber,
    Subscription,
    SubscriptionStatus,
    ParkingSession,
    SessionStatus,
    Shift,
    PricingRule,
)
from services.auth_service import AuthService


async def setup_operator_with_shift(db_session: AsyncSession, auth_service: AuthService, async_client):
    hashed = auth_service.hash_password("oppass")
    op = User(
        full_name="Ali Mahmoud",
        username="op_sub_test",
        hashed_password=hashed,
        role=UserRole.OPERATOR,
        gate_number=1,
        is_active=True,
    )
    db_session.add(op)
    await db_session.commit()
    await db_session.refresh(op)

    # Add active shift
    shift = Shift(
        operator_id=op.id,
        gate_number=1,
        started_at=datetime.utcnow(),
        opening_cash_egp=10000,
    )
    # Add active pricing rule
    rule = PricingRule(
        label="Standard",
        rate_per_hour=1000,
        grace_period_mins=15,
        lost_card_penalty=5000,
        minimum_charge=1000,
        effective_from=datetime.utcnow() - timedelta(days=1),
        is_active=True,
        created_by=op.id,
    )
    db_session.add_all([shift, rule])
    await db_session.commit()

    token = auth_service.create_access_token(op.id, op.role.value)
    async_client.cookies.set("pgms_token", token)
    return op, shift


@pytest.mark.asyncio
async def test_operator_subscribed_card_entry_flow(
    async_client,
    db_session: AsyncSession,
    auth_service: AuthService,
):
    op, shift = await setup_operator_with_shift(db_session, auth_service, async_client)

    # Setup card in use for subscription
    card = ParkingCard(card_code="CARD-SUB-01", status=CardStatus.IN_USE)
    plan = SubscriptionPlan(
        label="Monthly 1 Entry",
        duration_days=30,
        price_piastres=50000,
        max_entries_per_day=1,
        is_active=True,
        created_by=op.id,
    )
    subr = Subscriber(
        full_name="Tarek Zaki",
        plate_number="أ ب ج 9876",
        phone_number="01011112222",
    )
    db_session.add_all([card, plan, subr])
    await db_session.commit()
    await db_session.refresh(card)
    await db_session.refresh(plan)
    await db_session.refresh(subr)

    today = date.today()
    sub = Subscription(
        subscriber_id=subr.id,
        plan_id=plan.id,
        card_id=card.id,
        plate_number=subr.plate_number,
        start_date=today,
        end_date=today + timedelta(days=30),
        status=SubscriptionStatus.ACTIVE,
        amount_paid_piastres=50000,
        plan_price_snapshot=50000,
        collected_by=op.id,
    )
    db_session.add(sub)
    await db_session.commit()

    # 1. Post entry scan with subscribed card
    res = await async_client.post(
        "/ui/operator/entry",
        data={"card_code": "CARD-SUB-01", "plate_number": "أ ب ج 9876"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    confirm_url = res.headers["location"]
    assert "/ui/operator/entry/confirm/" in confirm_url

    # 2. Get entry confirmation screen -> check subscribed banner
    res_conf = await async_client.get(confirm_url)
    assert res_conf.status_code == 200
    assert "مشترك" in res_conf.text
    assert "Tarek Zaki" in res_conf.text

    # Extract session_id
    session_id = int(confirm_url.split("/")[-1])

    # Switch operator to exit gate 2 with an active shift
    op.gate_number = 2
    shift_exit = Shift(
        operator_id=op.id,
        gate_number=2,
        started_at=datetime.utcnow(),
        opening_cash_egp=10000,
    )
    db_session.add(shift_exit)
    await db_session.commit()
    token = auth_service.create_access_token(op.id, op.role.value)
    async_client.cookies.set("pgms_token", token)

    # 3. Post exit lookup for the subscribed card
    res_lookup = await async_client.post(
        "/ui/operator/exit/lookup",
        data={"card_code": "CARD-SUB-01"},
    )
    assert res_lookup.status_code == 200
    assert "مشترك نشط" in res_lookup.text
    assert "٠ ج.م" in res_lookup.text
    assert "تأكيد الخروج (اشتراك)" in res_lookup.text

    # 4. Confirm exit -> zero-charge exit and redirect to receipt
    res_exit_conf = await async_client.post(
        f"/ui/operator/exit/{session_id}/confirm",
        follow_redirects=False,
    )
    assert res_exit_conf.status_code == 303
    assert res_exit_conf.headers["location"] == f"/ui/operator/receipt/{session_id}"

    # 5. Get receipt -> renders thermal_subscription.html
    res_receipt = await async_client.get(f"/ui/operator/receipt/{session_id}")
    assert res_receipt.status_code == 200
    assert "إيصال اشتراك" in res_receipt.text
    assert "Tarek Zaki" in res_receipt.text
    assert "Monthly 1 Entry" in res_receipt.text
    assert "٠ ج.م" in res_receipt.text

    # 6. Second entry today exceeds max_entries_per_day=1 -> fails with error banner
    op.gate_number = 1
    await db_session.commit()
    token = auth_service.create_access_token(op.id, op.role.value)
    async_client.cookies.set("pgms_token", token)

    res_second_entry = await async_client.post(
        "/ui/operator/entry",
        data={"card_code": "CARD-SUB-01"},
    )
    assert res_second_entry.status_code == 200
    assert "تم الوصول للحد الأقصى" in res_second_entry.text


@pytest.mark.asyncio
async def test_operator_cannot_create_or_renew_subscriptions(
    async_client,
    db_session: AsyncSession,
    auth_service: AuthService,
):
    op, shift = await setup_operator_with_shift(db_session, auth_service, async_client)

    # 1. Create active plan and available card
    card = ParkingCard(card_code="CARD-POS-01", status=CardStatus.AVAILABLE)
    plan = SubscriptionPlan(
        label="باقة شهرية POS",
        duration_days=30,
        price_piastres=45000,
        max_entries_per_day=None,
        is_active=True,
        created_by=op.id,
    )
    subr = Subscriber(
        full_name="مروان إبراهيم",
        plate_number="س س س 555",
        phone_number="01055554444",
    )
    db_session.add_all([card, plan, subr])
    await db_session.commit()
    await db_session.refresh(card)
    await db_session.refresh(plan)
    await db_session.refresh(subr)

    # 2. Check Dashboard does NOT have new subscription button
    res_dash = await async_client.get("/ui/operator/dashboard")
    assert res_dash.status_code == 200
    assert "اشتراك جديد" not in res_dash.text
    assert "/ui/operator/subscriptions/new" not in res_dash.text

    # 3. Accessing /ui/operator/subscriptions/new returns 404 (route removed)
    res_form = await async_client.get("/ui/operator/subscriptions/new")
    assert res_form.status_code == 404

    # 4. Attempting to create subscription via API as operator returns 403 Forbidden
    res_api_create = await async_client.post(
        "/api/v1/subscriptions/",
        json={
            "subscriber_id": subr.id,
            "plan_id": plan.id,
            "card_id": card.id,
            "start_date": str(date.today()),
            "amount_paid_egp": 450.0,
        },
    )
    assert res_api_create.status_code == 403

    # 5. Attempting to renew subscription via API as operator returns 403 Forbidden
    sub = Subscription(
        subscriber_id=subr.id,
        plan_id=plan.id,
        card_id=card.id,
        plate_number=subr.plate_number,
        start_date=date.today() - timedelta(days=31),
        end_date=date.today() - timedelta(days=1),
        status=SubscriptionStatus.EXPIRED,
        amount_paid_piastres=45000,
        plan_price_snapshot=45000,
        collected_by=op.id,
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)

    res_api_renew = await async_client.post(
        f"/api/v1/subscriptions/{sub.id}/renew",
        json={
            "plan_id": plan.id,
            "amount_paid_egp": 450.0,
        },
    )
    assert res_api_renew.status_code == 403
