import hashlib
import pytest
from datetime import datetime, timedelta
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from dependencies import get_current_user, get_tenant_context
from fastapi import FastAPI
from routes.platform_admin import router as platform_admin_router
from routes.owner_api import router as owner_api_router
from routes.garage_management_api import router as garage_management_api_router
from routes.sessions import router as sessions_router
from routes.cards import router as cards_router
from routes.rates import router as rates_router
from models.owner import Owner
from models.garage import Garage
from models.garage_config import GarageConfig
from models.garage_gate import GarageGate
from models.user import User, UserRole
from models.shift import Shift
from models.parking_session import ParkingSession, SessionStatus
from models.parking_card import ParkingCard, CardStatus
from models.pricing_rule import PricingRule
from models.edge_device import EdgeDevice
from services.auth_service import AuthService


@pytest.fixture
def test_app(db_session: AsyncSession):
    fastapi_app = FastAPI()
    fastapi_app.include_router(platform_admin_router)
    fastapi_app.include_router(owner_api_router)
    fastapi_app.include_router(garage_management_api_router)
    fastapi_app.include_router(sessions_router)
    fastapi_app.include_router(cards_router)
    fastapi_app.include_router(rates_router)
    fastapi_app.dependency_overrides[get_db] = lambda: db_session
    return fastapi_app


@pytest.fixture
async def api_test_env(db_session: AsyncSession):
    """Sets up a multi-tenant environment with 2 owners, 2 garages, and various role users."""
    auth_svc = AuthService(get_settings())

    # 1. Platform Super Admin
    super_admin = User(
        username="super_admin_api",
        full_name="Platform Super Admin",
        role=UserRole.SUPER_ADMIN,
        hashed_password=auth_svc.hash_password("SuperSecret1!"),
        is_active=True,
    )
    db_session.add(super_admin)
    await db_session.flush()

    # 2. Owner 1 (Quota: 1 garage allowed)
    owner1 = Owner(
        name="Owner One Fleet",
        contact_phone="+201011111111",
        contact_email="owner1_api@garage4u.com",
        subscription_plan="STANDARD",
        billing_status="ACTIVE",
        max_garages_allowed=1,
    )
    # Owner 2 (Quota: 2 garages allowed)
    owner2 = Owner(
        name="Owner Two Fleet",
        contact_phone="+201022222222",
        contact_email="owner2_api@garage4u.com",
        subscription_plan="PRO",
        billing_status="ACTIVE",
        max_garages_allowed=2,
    )
    db_session.add_all([owner1, owner2])
    await db_session.flush()

    # 3. Owner Users
    owner_user1 = User(
        username="owner_user_1",
        full_name="Owner User One",
        role=UserRole.OWNER,
        owner_id=owner1.id,
        hashed_password=auth_svc.hash_password("OwnerSecret1!"),
        is_active=True,
    )
    owner_user2 = User(
        username="owner_user_2",
        full_name="Owner User Two",
        role=UserRole.OWNER,
        owner_id=owner2.id,
        hashed_password=auth_svc.hash_password("OwnerSecret2!"),
        is_active=True,
    )
    db_session.add_all([owner_user1, owner_user2])
    await db_session.flush()

    # 4. Garages (Garage 1 for Owner 1, Garage 2 for Owner 2)
    garage1 = Garage(
        owner_id=owner1.id,
        code="GAR_API_1",
        name="Central Station Garage",
        capacity=100,
        status="ACTIVE",
        can_use_anpr=True,
        can_use_subscriptions=True,
        max_gates=2,
    )
    garage2 = Garage(
        owner_id=owner2.id,
        code="GAR_API_2",
        name="Airport Express Garage",
        capacity=200,
        status="ACTIVE",
        can_use_anpr=True,
        can_use_subscriptions=True,
        max_gates=4,
    )
    db_session.add_all([garage1, garage2])
    await db_session.flush()

    # 5. Configurations
    cfg1 = GarageConfig(
        garage_id=garage1.id,
        anpr_enabled=True,
        subscriptions_enabled=True,
        ticket_system="REUSABLE_CARDS",
        version=1,
    )
    cfg2 = GarageConfig(
        garage_id=garage2.id,
        anpr_enabled=True,
        subscriptions_enabled=True,
        ticket_system="REUSABLE_CARDS",
        version=1,
    )
    db_session.add_all([cfg1, cfg2])
    await db_session.flush()

    # 6. Gates (Gate 1 for each garage)
    gate1_g1 = GarageGate(
        garage_id=garage1.id,
        gate_number=1,
        name="G1 Main Gate",
        gate_type="COMBINED",
        paper_width_mm=80,
    )
    gate1_g2 = GarageGate(
        garage_id=garage2.id,
        gate_number=1,
        name="G2 Terminal Gate",
        gate_type="COMBINED",
        paper_width_mm=80,
    )
    db_session.add_all([gate1_g1, gate1_g2])
    await db_session.flush()

    # 7. Managers
    mgr1 = User(
        username="mgr_g1",
        full_name="Manager Garage 1",
        role=UserRole.MANAGER,
        owner_id=owner1.id,
        garage_id=garage1.id,
        hashed_password=auth_svc.hash_password("MgrPass123!"),
        is_active=True,
    )
    mgr2 = User(
        username="mgr_g2",
        full_name="Manager Garage 2",
        role=UserRole.MANAGER,
        owner_id=owner2.id,
        garage_id=garage2.id,
        hashed_password=auth_svc.hash_password("MgrPass123!"),
        is_active=True,
    )
    db_session.add_all([mgr1, mgr2])
    await db_session.flush()

    # 8. Operators
    op1 = User(
        username="op_g1",
        full_name="Operator Garage 1",
        role=UserRole.OPERATOR,
        owner_id=owner1.id,
        garage_id=garage1.id,
        gate_number=1,
        hashed_password=auth_svc.hash_password("OpPass123!"),
        is_active=True,
    )
    op1_idle = User(
        username="op_g1_idle",
        full_name="Idle Operator Garage 1",
        role=UserRole.OPERATOR,
        owner_id=owner1.id,
        garage_id=garage1.id,
        gate_number=1,
        hashed_password=auth_svc.hash_password("OpPass123!"),
        is_active=True,
    )
    op2 = User(
        username="op_g2",
        full_name="Operator Garage 2",
        role=UserRole.OPERATOR,
        owner_id=owner2.id,
        garage_id=garage2.id,
        gate_number=1,
        hashed_password=auth_svc.hash_password("OpPass123!"),
        is_active=True,
    )
    db_session.add_all([op1, op1_idle, op2])
    await db_session.flush()

    # 9. Shifts (Open shift for op1 and op2)
    shift1 = Shift(
        garage_id=garage1.id,
        operator_id=op1.id,
        gate_number=1,
        started_at=datetime.utcnow() - timedelta(hours=2),
        opening_cash_egp=50,
    )
    shift2 = Shift(
        garage_id=garage2.id,
        operator_id=op2.id,
        gate_number=1,
        started_at=datetime.utcnow() - timedelta(hours=2),
        opening_cash_egp=50,
    )
    db_session.add_all([shift1, shift2])
    await db_session.flush()

    # 10. Pricing rules
    rule1 = PricingRule(
        garage_id=garage1.id,
        label="Standard G1 Rate",
        rate_per_hour=15,
        first_hour_charge=15,
        subsequent_hour_charge=15,
        minimum_charge=15,
        grace_period_mins=10,
        lost_card_penalty=50,
        effective_from=datetime.utcnow() - timedelta(days=5),
        created_by=mgr1.id,
        is_active=True,
    )
    rule2 = PricingRule(
        garage_id=garage2.id,
        label="Standard G2 Rate",
        rate_per_hour=25,
        first_hour_charge=25,
        subsequent_hour_charge=25,
        minimum_charge=25,
        grace_period_mins=10,
        lost_card_penalty=100,
        effective_from=datetime.utcnow() - timedelta(days=5),
        created_by=mgr2.id,
        is_active=True,
    )
    db_session.add_all([rule1, rule2])
    await db_session.flush()

    # 11. Cards
    card1 = ParkingCard(
        card_code="CARD-G1-0001",
        garage_id=garage1.id,
        status=CardStatus.IN_USE,
    )
    card1_avail = ParkingCard(
        card_code="CARD-G1-0002",
        garage_id=garage1.id,
        status=CardStatus.AVAILABLE,
    )
    card2 = ParkingCard(
        card_code="CARD-G2-0001",
        garage_id=garage2.id,
        status=CardStatus.IN_USE,
    )
    db_session.add_all([card1, card1_avail, card2])
    await db_session.flush()

    # 12. Parking Sessions
    session1 = ParkingSession(
        garage_id=garage1.id,
        receipt_no=101,
        card_id=card1.id,
        card_code=card1.card_code,
        gate_number=1,
        shift_id=shift1.id,
        operator_id=op1.id,
        plate_number="ABC-100",
        entry_time=datetime.utcnow() - timedelta(minutes=45),
        status=SessionStatus.ACTIVE,
    )
    session2 = ParkingSession(
        garage_id=garage2.id,
        receipt_no=201,
        card_id=card2.id,
        card_code=card2.card_code,
        gate_number=1,
        shift_id=shift2.id,
        operator_id=op2.id,
        plate_number="XYZ-200",
        entry_time=datetime.utcnow() - timedelta(minutes=20),
        status=SessionStatus.ACTIVE,
    )
    db_session.add_all([session1, session2])
    await db_session.flush()

    # 13. Edge Device in Garage 1
    raw_token_dev = "edge_device_secret_initial_token_xyz"
    token_hash_dev = hashlib.sha256(raw_token_dev.encode()).hexdigest()
    device1 = EdgeDevice(
        garage_id=garage1.id,
        device_name="Cam-G1-Entry",
        token_hash=token_hash_dev,
    )
    db_session.add(device1)
    await db_session.flush()

    # Generate Auth tokens
    tokens = {
        "super_admin": auth_svc.create_access_token(super_admin.id, super_admin.role.value),
        "owner1": auth_svc.create_access_token(owner_user1.id, owner_user1.role.value),
        "owner2": auth_svc.create_access_token(owner_user2.id, owner_user2.role.value),
        "manager1": auth_svc.create_access_token(mgr1.id, mgr1.role.value),
        "operator1": auth_svc.create_access_token(op1.id, op1.role.value),
        "operator1_idle": auth_svc.create_access_token(op1_idle.id, op1_idle.role.value),
        "operator2": auth_svc.create_access_token(op2.id, op2.role.value),
    }

    return {
        "super_admin": super_admin,
        "owner1": owner1,
        "owner2": owner2,
        "owner_user1": owner_user1,
        "owner_user2": owner_user2,
        "garage1": garage1,
        "garage2": garage2,
        "manager1": mgr1,
        "manager2": mgr2,
        "operator1": op1,
        "operator1_idle": op1_idle,
        "operator2": op2,
        "shift1": shift1,
        "shift2": shift2,
        "session1": session1,
        "session2": session2,
        "card1_avail": card1_avail,
        "device1": device1,
        "tokens": tokens,
    }


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# Task 9.1: Super Admin Routes & Garage Quota Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_platform_admin_create_owner_and_list(test_app: FastAPI, api_test_env: dict):
    """Verifies Super Admin endpoints for managing owners and listing audit trails."""
    tokens = api_test_env["tokens"]
    sa_token = tokens["super_admin"]
    op_token = tokens["operator1"]

    transport = ASGITransport(app=test_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Non-super-admin denied
        res_denied = await client.get("/api/v1/platform/owners", headers=_auth_headers(op_token))
        assert res_denied.status_code == 403
        assert res_denied.headers.get("X-Error-Code") == "INSUFFICIENT_PERMISSIONS"

        # 2. Super admin creates owner
        res_create = await client.post(
            "/api/v1/platform/owners",
            headers=_auth_headers(sa_token),
            json={
                "name": "Alexandria Fleet Group",
                "company_name": "Alex Parking SAE",
                "contact_phone": "+201033333333",
                "contact_email": "alex_fleet@garage4u.com",
                "subscription_plan": "ENTERPRISE",
                "billing_status": "ACTIVE",
                "max_garages_allowed": 5,
            },
        )
        assert res_create.status_code == 201
        created = res_create.json()
        assert created["name"] == "Alexandria Fleet Group"
        assert created["max_garages_allowed"] == 5

        # 3. Super admin lists owners
        res_list = await client.get("/api/v1/platform/owners", headers=_auth_headers(sa_token))
        assert res_list.status_code == 200
        payload = res_list.json()
        assert payload["total"] >= 3
        emails = [o["contact_email"] for o in payload["data"]]
        assert "alex_fleet@garage4u.com" in emails

        # 4. Super admin audit trail
        res_audit = await client.get("/api/v1/platform/audit-trail", headers=_auth_headers(sa_token))
        assert res_audit.status_code == 200
        assert "data" in res_audit.json()


@pytest.mark.asyncio
async def test_platform_admin_garage_quota_enforcement(test_app: FastAPI, api_test_env: dict):
    """Verifies strict quota enforcement (422 MAX_GARAGES_EXCEEDED) when owner reaches max_garages_allowed."""
    tokens = api_test_env["tokens"]
    sa_token = tokens["super_admin"]
    owner1 = api_test_env["owner1"]  # Has max_garages_allowed = 1, already has Garage 1
    owner2 = api_test_env["owner2"]  # Has max_garages_allowed = 2, currently has 1 garage

    transport = ASGITransport(app=test_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Attempt to add a 2nd garage to Owner 1 (Quota exceeded!)
        res_exceeded = await client.post(
            "/api/v1/platform/garages",
            headers=_auth_headers(sa_token),
            json={
                "owner_id": owner1.id,
                "code": "GAR_OVER_QUOTA",
                "name": "Second Garage Owner 1",
                "capacity": 50,
                "can_use_anpr": True,
                "can_use_subscriptions": True,
                "max_gates": 2,
            },
        )
        assert res_exceeded.status_code == 422
        assert res_exceeded.headers.get("X-Error-Code") == "MAX_GARAGES_EXCEEDED"
        assert "maximum allowed garages limit" in res_exceeded.json()["detail"]

        # 2. Add a 2nd garage to Owner 2 (Within quota: 1 -> 2 out of 2 allowed)
        res_success = await client.post(
            "/api/v1/platform/garages",
            headers=_auth_headers(sa_token),
            json={
                "owner_id": owner2.id,
                "code": "GAR_OWNER2_OK",
                "name": "Second Garage Owner 2",
                "capacity": 150,
                "can_use_anpr": False,
                "can_use_subscriptions": True,
                "max_gates": 3,
            },
        )
        assert res_success.status_code == 201
        created_g = res_success.json()
        assert created_g["code"] == "GAR_OWNER2_OK"
        assert created_g["owner_id"] == owner2.id


# ===========================================================================
# Task 9.2: Owner Routes & Cross-Owner Isolation Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_owner_routes_and_cross_owner_isolation(test_app: FastAPI, api_test_env: dict):
    """Verifies that an owner can only see their own garages and fleet dashboard."""
    tokens = api_test_env["tokens"]
    owner1_token = tokens["owner1"]
    owner2_token = tokens["owner2"]
    garage1 = api_test_env["garage1"]
    garage2 = api_test_env["garage2"]

    transport = ASGITransport(app=test_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Owner 1 lists garages -> Only Garage 1 returned
        res_g1 = await client.get("/api/v1/owner/garages", headers=_auth_headers(owner1_token))
        assert res_g1.status_code == 200
        garages_g1 = res_g1.json()
        assert len(garages_g1) == 1
        assert garages_g1[0]["id"] == garage1.id
        assert garages_g1[0]["code"] == garage1.code

        # 2. Owner 2 lists garages -> Only Garage 2 returned
        res_g2 = await client.get("/api/v1/owner/garages", headers=_auth_headers(owner2_token))
        assert res_g2.status_code == 200
        garages_g2 = res_g2.json()
        assert any(g["id"] == garage2.id for g in garages_g2)
        assert all(g["id"] != garage1.id for g in garages_g2)

        # 3. Owner 1 dashboard -> aggregated metrics only for Garage 1
        res_dash = await client.get("/api/v1/owner/dashboard", headers=_auth_headers(owner1_token))
        assert res_dash.status_code == 200
        dash = res_dash.json()
        assert dash["total_garages"] == 1
        assert dash["total_capacity"] == garage1.capacity
        assert dash["total_active_sessions"] == 1
        assert dash["garages"][0]["id"] == garage1.id


# ===========================================================================
# Task 9.3: Garage Management API & IDOR Protection Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_garage_management_idor_protection_returns_404(test_app: FastAPI, api_test_env: dict):
    """Verifies that accessing or modifying another garage returns 404 TENANT_NOT_FOUND."""
    tokens = api_test_env["tokens"]
    mgr1_token = tokens["manager1"]  # Belongs to Garage 1
    garage2 = api_test_env["garage2"]
    session2 = api_test_env["session2"]

    transport = ASGITransport(app=test_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Manager 1 attempts to read Garage 2 config -> 404 TENANT_NOT_FOUND
        res_cfg = await client.get(
            f"/api/v1/garages/{garage2.id}/config",
            headers=_auth_headers(mgr1_token),
        )
        assert res_cfg.status_code == 404
        assert res_cfg.headers.get("X-Error-Code") == "TENANT_NOT_FOUND"

        # 2. Manager 1 attempts to list Garage 2 gates -> 404 TENANT_NOT_FOUND
        res_gates = await client.get(
            f"/api/v1/garages/{garage2.id}/gates",
            headers=_auth_headers(mgr1_token),
        )
        assert res_gates.status_code == 404
        assert res_gates.headers.get("X-Error-Code") == "TENANT_NOT_FOUND"

        # 3. Manager 1 attempts to read Garage 2 session -> 404 TENANT_NOT_FOUND
        res_session = await client.get(
            f"/api/v1/garages/{garage2.id}/sessions/{session2.id}",
            headers=_auth_headers(mgr1_token),
        )
        assert res_session.status_code == 404
        assert res_session.headers.get("X-Error-Code") == "TENANT_NOT_FOUND"


@pytest.mark.asyncio
async def test_garage_gates_max_gates_quota_enforcement(test_app: FastAPI, api_test_env: dict):
    """Verifies that adding gates beyond max_gates raises 422 MAX_GATES_EXCEEDED."""
    tokens = api_test_env["tokens"]
    owner1_token = tokens["owner1"]
    garage1 = api_test_env["garage1"]  # max_gates = 2, currently has Gate 1

    transport = ASGITransport(app=test_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Add Gate 2 (count reaches 2/2) -> 201 Created
        res_gate2 = await client.post(
            f"/api/v1/garages/{garage1.id}/gates",
            headers=_auth_headers(owner1_token),
            json={
                "gate_number": 2,
                "name": "G1 Exit Gate",
                "gate_type": "EXIT_ONLY",
                "paper_width_mm": 80,
            },
        )
        assert res_gate2.status_code == 201
        assert res_gate2.json()["gate_number"] == 2

        # 2. Attempt to add Gate 3 (Exceeds max_gates = 2) -> 422 MAX_GATES_EXCEEDED
        res_gate3 = await client.post(
            f"/api/v1/garages/{garage1.id}/gates",
            headers=_auth_headers(owner1_token),
            json={
                "gate_number": 3,
                "name": "G1 Extra Gate",
                "gate_type": "ENTRY_ONLY",
                "paper_width_mm": 80,
            },
        )
        assert res_gate3.status_code == 422
        assert res_gate3.headers.get("X-Error-Code") == "MAX_GATES_EXCEEDED"
        assert "Cannot exceed maximum gate limit" in res_gate3.json()["detail"]


@pytest.mark.asyncio
async def test_edge_device_registration_and_token_rotation(test_app: FastAPI, api_test_env: dict):
    """Verifies edge device creation, initial raw token generation, and token rotation."""
    tokens = api_test_env["tokens"]
    owner1_token = tokens["owner1"]
    garage1 = api_test_env["garage1"]

    transport = ASGITransport(app=test_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Register new edge device
        res_create = await client.post(
            f"/api/v1/garages/{garage1.id}/edge-devices",
            headers=_auth_headers(owner1_token),
            json={"device_name": "Gate 2 ANPR Camera PC"},
        )
        assert res_create.status_code == 201
        dev_data = res_create.json()
        device_id = dev_data["device"]["id"]
        raw_token_1 = dev_data["raw_token"]
        token_hash_1 = dev_data["device"]["token_hash"]

        # Verify hash match
        assert hashlib.sha256(raw_token_1.encode()).hexdigest() == token_hash_1

        # 2. Rotate token
        res_rotate = await client.post(
            f"/api/v1/garages/{garage1.id}/edge-devices/{device_id}/rotate-token",
            headers=_auth_headers(owner1_token),
        )
        assert res_rotate.status_code == 200
        rot_data = res_rotate.json()
        raw_token_2 = rot_data["raw_token"]
        token_hash_2 = rot_data["device"]["token_hash"]

        assert raw_token_2 != raw_token_1
        assert token_hash_2 != token_hash_1
        assert hashlib.sha256(raw_token_2.encode()).hexdigest() == token_hash_2


@pytest.mark.asyncio
async def test_plate_override_rbac_and_operator_shift_guard(test_app: FastAPI, api_test_env: dict):
    """Verifies plate-override RBAC: allowed for manager, allowed for operator with open shift,
    and blocked with 403 for operator without open shift.
    """
    tokens = api_test_env["tokens"]
    mgr1_token = tokens["manager1"]
    op1_token = tokens["operator1"]            # has active shift in Garage 1
    op1_idle_token = tokens["operator1_idle"]   # NO active shift
    garage1 = api_test_env["garage1"]
    session1 = api_test_env["session1"]

    transport = ASGITransport(app=test_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Manager overrides plate -> 200 OK
        res_mgr = await client.post(
            f"/api/v1/garages/{garage1.id}/sessions/{session1.id}/plate-override",
            headers=_auth_headers(mgr1_token),
            json={"plate_number": "MGR-1111", "override_reason": "ANPR typo correction"},
        )
        assert res_mgr.status_code == 200
        assert res_mgr.json()["plate_number"] == "MGR-1111"

        # 2. Operator with open shift overrides plate -> 200 OK
        res_op = await client.post(
            f"/api/v1/garages/{garage1.id}/sessions/{session1.id}/plate-override",
            headers=_auth_headers(op1_token),
            json={"plate_number": "OP-2222", "override_reason": "Verified manually"},
        )
        assert res_op.status_code == 200
        assert res_op.json()["plate_number"] == "OP-2222"

        # 3. Operator WITHOUT active shift attempts plate override -> 403 OPERATOR_SHIFT_REQUIRED
        res_idle = await client.post(
            f"/api/v1/garages/{garage1.id}/sessions/{session1.id}/plate-override",
            headers=_auth_headers(op1_idle_token),
            json={"plate_number": "HACK-9999"},
        )
        assert res_idle.status_code == 403
        assert res_idle.headers.get("X-Error-Code") == "OPERATOR_SHIFT_REQUIRED"


# ===========================================================================
# Task 9.4: Field Operations Automatic Garage Extraction
# ===========================================================================

@pytest.mark.asyncio
async def test_field_operations_automatic_garage_extraction(test_app: FastAPI, api_test_env: dict):
    """Verifies that field operations (sessions, cards) automatically bind to the operator's garage."""
    tokens = api_test_env["tokens"]
    op1_token = tokens["operator1"]
    garage1 = api_test_env["garage1"]
    card1_avail = api_test_env["card1_avail"]

    transport = ASGITransport(app=test_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Operator 1 opens session without sending garage_id in URL or body
        res = await client.post(
            "/api/v1/sessions/",
            headers=_auth_headers(op1_token),
            json={
                "card_code": card1_avail.card_code,
                "plate_number": "FIELD-01",
            },
        )
        assert res.status_code == 201
        session_data = res.json()["data"]

        # Garage ID is automatically bound to Operator 1's garage
        assert session_data["garage_id"] == garage1.id
        assert session_data["operator_id"] == api_test_env["operator1"].id
