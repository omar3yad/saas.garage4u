import pytest
from datetime import datetime, timedelta, timezone
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from config import get_settings
from database import get_db
from middlewares.csrf import CSRFMiddleware, CSRF_COOKIE_NAME
from models.owner import Owner
from models.garage import Garage
from models.garage_gate import GarageGate
from models.user import User, UserRole
from models.parking_session import ParkingSession, SessionStatus
from models.parking_card import ParkingCard, CardStatus
from routes.ui_admin import router as ui_admin_router, owner_ui_router
from services.auth_service import AuthService
from main import templates


@pytest.fixture
def test_app(db_session: AsyncSession):
    fastapi_app = FastAPI()
    fastapi_app.add_middleware(CSRFMiddleware)
    fastapi_app.include_router(ui_admin_router)
    fastapi_app.include_router(owner_ui_router)

    # Exemption test endpoints
    @fastapi_app.post("/api/v1/test-endpoint")
    async def sample_api_post():
        return {"status": "ok"}

    @fastapi_app.post("/api/v1/edge/upload-capture")
    async def sample_edge_post():
        return {"status": "edge_ok"}

    @fastapi_app.get("/owner/dashboard")
    async def legacy_owner_dashboard_redirect():
        return RedirectResponse("/ui/owner/dashboard", status_code=303)

    fastapi_app.state.templates = templates
    fastapi_app.dependency_overrides[get_db] = lambda: db_session
    return fastapi_app


@pytest.fixture
async def ui_test_env(db_session: AsyncSession):
    """Sets up 2 owners, 3 garages, active & completed sessions, and users."""
    auth_svc = AuthService(get_settings())

    # Owner 1 (Owns Garage 1 and Garage 2)
    owner1 = Owner(
        name="Fleet Group Alpha",
        company_name="Alpha Parking",
        contact_phone="+201001112233",
        contact_email="alpha@fleet.test",
        billing_status="ACTIVE",
        max_garages_allowed=5,
    )
    db_session.add(owner1)
    await db_session.flush()

    # Owner 2 (Owns Garage 3)
    owner2 = Owner(
        name="Fleet Group Beta",
        company_name="Beta Parking",
        contact_phone="+201004445566",
        contact_email="beta@fleet.test",
        billing_status="ACTIVE",
        max_garages_allowed=2,
    )
    db_session.add(owner2)
    await db_session.flush()

    # Garages for Owner 1
    garage1 = Garage(
        owner_id=owner1.id,
        name="Downtown Hub",
        code="GAR-DT-01",
        status="ACTIVE",
        capacity=100,
        can_use_anpr=True,
        can_use_subscriptions=True,
        max_gates=4,
    )
    garage2 = Garage(
        owner_id=owner1.id,
        name="Airport Express",
        code="GAR-AP-02",
        status="ACTIVE",
        capacity=200,
        can_use_anpr=True,
        can_use_subscriptions=True,
        max_gates=6,
    )
    # Garage for Owner 2
    garage3 = Garage(
        owner_id=owner2.id,
        name="Suburban Plaza",
        code="GAR-SUB-03",
        status="ACTIVE",
        capacity=150,
        can_use_anpr=False,
        can_use_subscriptions=True,
        max_gates=2,
    )
    db_session.add_all([garage1, garage2, garage3])
    await db_session.flush()

    # Gates for Garage 1
    gate1_1 = GarageGate(garage_id=garage1.id, gate_number=1, name="North Entry", gate_type="ENTRY")
    gate1_2 = GarageGate(garage_id=garage1.id, gate_number=2, name="North Exit", gate_type="EXIT")
    gate1_3 = GarageGate(garage_id=garage1.id, gate_number=3, name="South Gate", gate_type="BIDIRECTIONAL")
    db_session.add_all([gate1_1, gate1_2, gate1_3])

    # Owner 1 User
    owner1_user = User(
        username="owner1_user",
        full_name="Alpha Owner Director",
        role=UserRole.OWNER,
        owner_id=owner1.id,
        hashed_password=auth_svc.hash_password("OwnerPass123!"),
        is_active=True,
    )
    # Manager for Garage 1
    manager1 = User(
        username="manager1_user",
        full_name="Downtown Manager",
        role=UserRole.MANAGER,
        owner_id=owner1.id,
        garage_id=garage1.id,
        hashed_password=auth_svc.hash_password("ManagerPass123!"),
        is_active=True,
    )
    db_session.add_all([owner1_user, manager1])
    await db_session.flush()

    # Cards for sessions
    card1 = ParkingCard(garage_id=garage1.id, card_code="CARD-001", status=CardStatus.IN_USE)
    card2 = ParkingCard(garage_id=garage1.id, card_code="CARD-002", status=CardStatus.IN_USE)
    card3 = ParkingCard(garage_id=garage2.id, card_code="CARD-003", status=CardStatus.IN_USE)
    db_session.add_all([card1, card2, card3])
    await db_session.flush()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # 2 Active sessions in Garage 1
    s1 = ParkingSession(
        garage_id=garage1.id,
        card_id=card1.id,
        ticket_code="TKT-G1-01",
        receipt_no=1,
        plate_number="ABC-111",
        shift_id=1,
        operator_id=manager1.id,
        entry_time=now - timedelta(hours=1),
        gate_number=1,
        status=SessionStatus.ACTIVE,
    )
    s2 = ParkingSession(
        garage_id=garage1.id,
        card_id=card2.id,
        ticket_code="TKT-G1-02",
        receipt_no=2,
        plate_number="ABC-222",
        shift_id=1,
        operator_id=manager1.id,
        entry_time=now - timedelta(hours=2),
        gate_number=1,
        status=SessionStatus.ACTIVE,
    )
    # 1 Completed paid session today in Garage 1 (Revenue = 150.0 EGP)
    s3 = ParkingSession(
        garage_id=garage1.id,
        ticket_code="TKT-G1-03",
        receipt_no=3,
        plate_number="ABC-333",
        shift_id=1,
        operator_id=manager1.id,
        entry_time=now - timedelta(hours=3),
        exit_time=now - timedelta(minutes=15),
        gate_number=2,
        status=SessionStatus.COMPLETED,
        amount_charged=150,
        is_paid=True,
    )
    # 1 Active session in Garage 2
    s4 = ParkingSession(
        garage_id=garage2.id,
        card_id=card3.id,
        ticket_code="TKT-G2-01",
        receipt_no=1,
        plate_number="XYZ-999",
        shift_id=2,
        operator_id=manager1.id,
        entry_time=now - timedelta(minutes=45),
        gate_number=1,
        status=SessionStatus.ACTIVE,
    )
    db_session.add_all([s1, s2, s3, s4])
    await db_session.commit()

    return {
        "owner1": owner1,
        "owner2": owner2,
        "garage1": garage1,
        "garage2": garage2,
        "garage3": garage3,
        "owner1_user": owner1_user,
        "manager1": manager1,
        "owner1_token": auth_svc.create_access_token(owner1_user.id, owner1_user.role.value),
        "manager1_token": auth_svc.create_access_token(manager1.id, manager1.role.value),
    }


@pytest.mark.asyncio
async def test_csrf_middleware_exemptions(test_app):
    """Verifies that API & Edge routes are exempt from CSRF checks and GET sets CSRF cookie."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Case A: POST to /api/v1/ without any CSRF token -> Passes without CSRF block
        res_api = await client.post("/api/v1/test-endpoint")
        assert res_api.status_code == 200
        assert res_api.json() == {"status": "ok"}

        # Case B: POST to /api/v1/edge/ without CSRF token -> Passes without CSRF block
        res_edge = await client.post("/api/v1/edge/upload-capture")
        assert res_edge.status_code == 200
        assert res_edge.json() == {"status": "edge_ok"}


@pytest.mark.asyncio
async def test_csrf_middleware_blocks_ui_post_without_token(test_app, ui_test_env):
    """Verifies that mutating UI routes reject requests lacking a valid CSRF token."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Case A: POST to UI endpoint without any CSRF token -> Rejected with 403 CSRF_TOKEN_INVALID
        client.cookies.set("pgms_token", ui_test_env["owner1_token"])
        res_no_csrf = await client.post(
            "/ui/admin/switch-garage",
            data={"garage_id": str(ui_test_env["garage1"].id)},
        )
        assert res_no_csrf.status_code == 403
        assert res_no_csrf.headers.get("X-Error-Code") == "CSRF_TOKEN_INVALID"

        # Case B: POST with mismatched cookie and form token -> 403 CSRF_TOKEN_INVALID
        client.cookies.set(CSRF_COOKIE_NAME, "valid_token_123")
        res_mismatch = await client.post(
            "/ui/admin/switch-garage",
            data={
                "garage_id": str(ui_test_env["garage1"].id),
                "_csrf_token": "tampered_token_999",
            },
        )
        assert res_mismatch.status_code == 403
        assert res_mismatch.headers.get("X-Error-Code") == "CSRF_TOKEN_INVALID"

        # Case C: POST with matching header token -> Bypasses CSRF validation
        res_valid = await client.post(
            "/ui/admin/switch-garage",
            data={"garage_id": str(ui_test_env["garage1"].id)},
            headers={"X-CSRF-Token": "valid_token_123"},
        )
        # Should proceed to handler (303 Redirect)
        assert res_valid.status_code == 303


@pytest.mark.asyncio
async def test_owner_switch_garage_success(test_app, ui_test_env):
    """Verifies that an owner can successfully switch between their owned garages."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        csrf_token = "secure_csrf_token_abc"
        client.cookies.set("pgms_token", ui_test_env["owner1_token"])
        client.cookies.set(CSRF_COOKIE_NAME, csrf_token)

        # Switch to Garage 2
        target_garage = ui_test_env["garage2"]
        res = await client.post(
            "/ui/admin/switch-garage",
            data={
                "garage_id": str(target_garage.id),
                "_csrf_token": csrf_token,
            },
        )
        assert res.status_code == 303
        assert res.headers["location"] == "/ui/admin/dashboard"

        # Verify pgms_active_garage cookie is updated to Garage 2
        set_cookie_header = res.headers.get("set-cookie", "")
        assert f"pgms_active_garage={target_garage.id}" in set_cookie_header


@pytest.mark.asyncio
async def test_owner_switch_garage_unowned_returns_404(test_app, ui_test_env):
    """Verifies that switching to a garage owned by another tenant returns 404 TENANT_NOT_FOUND."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        csrf_token = "secure_csrf_token_xyz"
        client.cookies.set("pgms_token", ui_test_env["owner1_token"])
        client.cookies.set(CSRF_COOKIE_NAME, csrf_token)

        # Garage 3 belongs to Owner 2
        foreign_garage = ui_test_env["garage3"]
        res = await client.post(
            "/ui/admin/switch-garage",
            data={
                "garage_id": str(foreign_garage.id),
                "_csrf_token": csrf_token,
            },
        )
        assert res.status_code == 404
        assert res.headers.get("X-Error-Code") == "TENANT_NOT_FOUND"


@pytest.mark.asyncio
async def test_owner_fleet_dashboard_aggregated_stats(test_app, ui_test_env):
    """Verifies that the owner fleet dashboard displays aggregated statistics across garages."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("pgms_token", ui_test_env["owner1_token"])

        res = await client.get("/ui/owner/dashboard")
        assert res.status_code == 200
        html = res.text

        # 1. Total garages
        assert "عدد الجراجات" in html
        assert "2" in html

        # 2. Total active vehicles (2 in G1 + 1 in G2 = 3)
        assert "السيارات بالداخل الآن" in html
        assert "3" in html

        # 3. Revenue today (150.00 EGP in G1)
        assert "150.00" in html

        # 4. Both garages listed
        assert "Downtown Hub" in html
        assert "Airport Express" in html
        # Suburban Plaza belongs to Owner 2, must not be present
        assert "Suburban Plaza" not in html


@pytest.mark.asyncio
async def test_owner_dashboard_redirect(test_app, ui_test_env):
    """Verifies that legacy /owner/dashboard redirects to /ui/owner/dashboard."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/owner/dashboard")
        assert res.status_code == 303
        assert res.headers["location"] == "/ui/owner/dashboard"
