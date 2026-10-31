import pytest
from datetime import datetime, timedelta, timezone
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import FastAPI

from config import get_settings
from database import get_db
from dependencies import get_current_user, get_tenant_context
from models.owner import Owner
from models.garage import Garage
from models.garage_gate import GarageGate
from models.garage_config import GarageConfig
from models.user import User, UserRole
from models.shift import Shift
from models.parking_session import ParkingSession, SessionStatus
from models.parking_card import ParkingCard, CardStatus
from models.pricing_rule import PricingRule
from services.auth_service import AuthService
from routes.garage_management_api import router as garage_mgmt_router
from routes.cards import router as cards_router
from routes.rates import router as rates_router
from routes.shifts import router as shifts_router
from routes.sessions import router as sessions_router
from routes.users import router as users_router


@pytest.fixture
def test_app(db_session: AsyncSession):
    app = FastAPI()
    app.include_router(garage_mgmt_router)
    app.include_router(cards_router)
    app.include_router(rates_router)
    app.include_router(shifts_router)
    app.include_router(sessions_router)
    app.include_router(users_router)
    app.dependency_overrides[get_db] = lambda: db_session
    return app


@pytest.fixture
async def isolation_env(db_session: AsyncSession):
    auth_svc = AuthService(get_settings())

    # 1. Two separate owners
    owner_a = Owner(
        name="Owner A Group",
        company_name="Owner A Corp",
        contact_phone="+201011111111",
        contact_email="owner_a@tenant.test",
        billing_status="ACTIVE",
        max_garages_allowed=2,
    )
    owner_b = Owner(
        name="Owner B Group",
        company_name="Owner B Corp",
        contact_phone="+201022222222",
        contact_email="owner_b@tenant.test",
        billing_status="ACTIVE",
        max_garages_allowed=2,
    )
    db_session.add_all([owner_a, owner_b])
    await db_session.flush()

    # 2. Garage A & Garage B
    garage_a = Garage(
        owner_id=owner_a.id,
        name="Garage Alpha",
        code="GAR-ALPHA",
        status="ACTIVE",
        capacity=100,
        can_use_anpr=True,
        can_use_subscriptions=True,
        max_gates=4,
    )
    garage_b = Garage(
        owner_id=owner_b.id,
        name="Garage Beta",
        code="GAR-BETA",
        status="ACTIVE",
        capacity=150,
        can_use_anpr=False,
        can_use_subscriptions=True,
        max_gates=4,
    )
    db_session.add_all([garage_a, garage_b])
    await db_session.flush()

    # Configs
    cfg_a = GarageConfig(garage_id=garage_a.id)
    cfg_b = GarageConfig(garage_id=garage_b.id)
    db_session.add_all([cfg_a, cfg_b])

    # Gates
    gate_a = GarageGate(garage_id=garage_a.id, gate_number=1, name="Alpha Gate 1", gate_type="ENTRY")
    gate_b = GarageGate(garage_id=garage_b.id, gate_number=1, name="Beta Gate 1", gate_type="ENTRY")
    db_session.add_all([gate_a, gate_b])

    # Managers
    mgr_a = User(
        username="mgr_alpha",
        full_name="Manager Alpha",
        role=UserRole.MANAGER,
        owner_id=owner_a.id,
        garage_id=garage_a.id,
        hashed_password=auth_svc.hash_password("PassAlpha123!"),
        is_active=True,
    )
    mgr_b = User(
        username="mgr_beta",
        full_name="Manager Beta",
        role=UserRole.MANAGER,
        owner_id=owner_b.id,
        garage_id=garage_b.id,
        hashed_password=auth_svc.hash_password("PassBeta123!"),
        is_active=True,
    )
    db_session.add_all([mgr_a, mgr_b])
    await db_session.flush()

    # Cards
    card_a = ParkingCard(garage_id=garage_a.id, card_code="CARD-ALPHA-01", status=CardStatus.AVAILABLE)
    card_b = ParkingCard(garage_id=garage_b.id, card_code="CARD-BETA-01", status=CardStatus.AVAILABLE)
    db_session.add_all([card_a, card_b])

    # Rates
    rate_a = PricingRule(
        garage_id=garage_a.id,
        label="Standard Rate Alpha",
        rate_per_hour=10,
        first_hour_charge=10,
        subsequent_hour_charge=10,
        minimum_charge=10,
        grace_period_mins=15,
        lost_card_penalty=50,
        created_by=mgr_a.id,
        effective_from=(datetime.now(timezone.utc) - timedelta(days=2)).replace(tzinfo=None),
        is_active=True,
    )
    rate_b = PricingRule(
        garage_id=garage_b.id,
        label="Standard Rate Beta",
        rate_per_hour=15,
        first_hour_charge=15,
        subsequent_hour_charge=15,
        minimum_charge=15,
        grace_period_mins=15,
        lost_card_penalty=50,
        created_by=mgr_b.id,
        effective_from=(datetime.now(timezone.utc) - timedelta(days=2)).replace(tzinfo=None),
        is_active=True,
    )
    db_session.add_all([rate_a, rate_b])

    # Shifts
    shift_a = Shift(
        garage_id=garage_a.id,
        operator_id=mgr_a.id,
        gate_number=1,
        started_at=(datetime.now(timezone.utc) - timedelta(hours=2)).replace(tzinfo=None),
    )
    shift_b = Shift(
        garage_id=garage_b.id,
        operator_id=mgr_b.id,
        gate_number=1,
        started_at=(datetime.now(timezone.utc) - timedelta(hours=2)).replace(tzinfo=None),
    )
    db_session.add_all([shift_a, shift_b])
    await db_session.flush()

    # Sessions
    session_a = ParkingSession(
        garage_id=garage_a.id,
        card_id=card_a.id,
        ticket_code="TKT-A-01",
        receipt_no=1,
        shift_id=shift_a.id,
        operator_id=mgr_a.id,
        entry_time=(datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None),
        gate_number=1,
        status=SessionStatus.ACTIVE,
    )
    session_b = ParkingSession(
        garage_id=garage_b.id,
        card_id=card_b.id,
        ticket_code="TKT-B-01",
        receipt_no=1,
        shift_id=shift_b.id,
        operator_id=mgr_b.id,
        entry_time=(datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None),
        gate_number=1,
        status=SessionStatus.ACTIVE,
    )
    db_session.add_all([session_a, session_b])
    await db_session.commit()

    return {
        "garage_a": garage_a,
        "garage_b": garage_b,
        "mgr_a": mgr_a,
        "mgr_b": mgr_b,
        "card_a": card_a,
        "card_b": card_b,
        "rate_a": rate_a,
        "rate_b": rate_b,
        "shift_a": shift_a,
        "shift_b": shift_b,
        "session_a": session_a,
        "session_b": session_b,
        "token_mgr_a": auth_svc.create_access_token(mgr_a.id, mgr_a.role.value),
        "token_mgr_b": auth_svc.create_access_token(mgr_b.id, mgr_b.role.value),
    }


def _auth(token: str):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,endpoint_template,payload",
    [
        ("GET", "/api/v1/garages/{b_gid}/config", None),
        ("PUT", "/api/v1/garages/{b_gid}/config?expected_version=1", {"ticket_system": "REUSABLE_CARDS", "min_session_duration_seconds": 60}),
        ("GET", "/api/v1/garages/{b_gid}/gates", None),
        ("POST", "/api/v1/garages/{b_gid}/gates", {"gate_number": 2, "name": "Hack Gate", "gate_type": "ENTRY"}),
        ("GET", "/api/v1/garages/{b_gid}/edge-devices", None),
        ("POST", "/api/v1/garages/{b_gid}/edge-devices", {"device_code": "CAM-HACK", "name": "Rogue Cam", "device_type": "ANPR"}),
        ("GET", "/api/v1/garages/{b_gid}/sessions/{b_sid}", None),
        ("GET", "/api/v1/cards/{b_card_code}", None),
        ("PUT", "/api/v1/rates/{b_rate_id}", {"rate_per_hour": 99}),
        ("GET", "/api/v1/shifts/{b_shift_id}/summary", None),
        ("GET", "/api/v1/users/{b_user_id}", None),
        ("PATCH", "/api/v1/users/{b_user_id}", {"full_name": "Tampered Name"}),
    ],
)
async def test_cross_tenant_idor_returns_404_strictly(
    test_app: FastAPI,
    isolation_env: dict,
    method: str,
    endpoint_template: str,
    payload: dict | None,
):
    """Verifies that an authenticated actor from Tenant A accessing Tenant B's resource ALWAYS gets 404 TENANT_NOT_FOUND."""
    b_gid = isolation_env["garage_b"].id
    b_sid = isolation_env["session_b"].id
    b_card_code = isolation_env["card_b"].card_code
    b_rate_id = isolation_env["rate_b"].id
    b_shift_id = isolation_env["shift_b"].id
    b_user_id = isolation_env["mgr_b"].id

    endpoint = endpoint_template.format(
        b_gid=b_gid,
        b_sid=b_sid,
        b_card_code=b_card_code,
        b_rate_id=b_rate_id,
        b_shift_id=b_shift_id,
        b_user_id=b_user_id,
    )

    headers = _auth(isolation_env["token_mgr_a"])
    transport = ASGITransport(app=test_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        if method == "GET":
            res = await client.get(endpoint, headers=headers)
        elif method == "POST":
            res = await client.post(endpoint, headers=headers, json=payload)
        elif method == "PUT":
            res = await client.put(endpoint, headers=headers, json=payload)
        elif method == "PATCH":
            res = await client.patch(endpoint, headers=headers, json=payload)
        else:
            raise ValueError(f"Unsupported method {method}")

        # Strict IDOR Assertion: 404 Not Found (NEVER 403)
        assert res.status_code == 404, f"Failed for {method} {endpoint}: expected 404, got {res.status_code} ({res.text})"
        assert res.headers.get("X-Error-Code") in (
            "TENANT_NOT_FOUND",
            "NOT_FOUND",
            "USER_NOT_FOUND",
            "CARD_NOT_FOUND",
            "RATE_NOT_FOUND",
            "SHIFT_NOT_FOUND",
            "CONFIG_NOT_FOUND",
            "PRICING_RULE_NOT_FOUND",
        )


@pytest.mark.asyncio
async def test_non_rls_users_table_protection(test_app: FastAPI, isolation_env: dict):
    """Verifies that Manager A cannot view or list users from Tenant B on non-RLS users table."""
    headers = _auth(isolation_env["token_mgr_a"])
    transport = ASGITransport(app=test_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Listing users: Manager A should only see users of Garage A
        res = await client.get("/api/v1/users/", headers=headers)
        assert res.status_code == 200
        users_list = res.json().get("data", [])
        user_ids = [u["id"] for u in users_list]

        assert isolation_env["mgr_a"].id in user_ids
        assert isolation_env["mgr_b"].id not in user_ids

        # Attempt to access Manager B directly: 404 Not Found
        res_single = await client.get(f"/api/v1/users/{isolation_env['mgr_b'].id}", headers=headers)
        assert res_single.status_code == 404
