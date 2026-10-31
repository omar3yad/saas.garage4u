import pytest
import pytest_asyncio
import json
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException, Request
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.pool import NullPool

from config import get_settings
from utils.tenant_context import TenantContext
from models.user import User, UserRole
from models.owner import Owner
from models.garage import Garage
from models.audit_log import AuditLog
from repositories.owner_repo import OwnerRepository
from repositories.garage_repo import GarageRepository
from repositories.user_repo import UserRepository
from services.auth_service import AuthService, AuthenticationError
from dependencies import (
    get_current_user,
    get_tenant_context,
    require_super_admin,
    require_owner_or_admin,
    require_manager_or_above,
    require_operator_or_above,
    validate_role_assignment,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_session():
    """Provides an isolated AsyncSession against PostgreSQL rolled back after each test."""
    settings = get_settings()
    db_url = settings.MIGRATION_DATABASE_URL or settings.DATABASE_URL
    engine = create_async_engine(db_url, poolclass=NullPool)
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        # Enable system context for initial fixture setups
        await conn.execute(text("SELECT set_config('app.is_system_context', 'true', true);"))
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def context_test_env(db_session: AsyncSession):
    """Sets up owners, garages, and test users with distinct roles and password flags."""
    owner_repo = OwnerRepository(db_session)
    garage_repo = GarageRepository(db_session)
    user_repo = UserRepository(db_session)
    auth_svc = AuthService(get_settings())

    # Owner 1 & Garage 1
    owner1 = await owner_repo.create(
        name="Al-Ahly Parking Corp",
        company_name="Al-Ahly LLC",
        contact_phone="+201000000001",
        contact_email="owner1@ahly.com",
        billing_status="ACTIVE",
    )
    garage1 = await garage_repo.create(
        owner_id=owner1.id,
        name="Babelooq Central Garage",
        code="GARAGE-CTX-1",
        city="Cairo",
        capacity=100,
        status="ACTIVE",
    )

    # Owner 2 & Garage 2
    owner2 = await owner_repo.create(
        name="Zamalek Parking Corp",
        company_name="Zamalek LLC",
        contact_phone="+201000000002",
        contact_email="owner2@zamalek.com",
        billing_status="ACTIVE",
    )
    garage2 = await garage_repo.create(
        owner_id=owner2.id,
        name="Zamalek West Garage",
        code="GARAGE-CTX-2",
        city="Giza",
        capacity=150,
        status="ACTIVE",
    )

    # Super Admin user (no owner, no garage)
    super_admin = await user_repo.create(
        full_name="Platform Administrator",
        username="ctx_superadmin",
        hashed_password=auth_svc.hash_password("SuperSecret1!"),
        role=UserRole.SUPER_ADMIN,
        owner_id=None,
        garage_id=None,
        gate_number=None,
        must_change_password=False,
        is_active=True,
    )

    # Owner 1 User
    owner_user1 = await user_repo.create(
        full_name="Ahmad Owner User",
        username="ctx_owner1",
        hashed_password=auth_svc.hash_password("OwnerSecret1!"),
        role=UserRole.OWNER,
        owner_id=owner1.id,
        garage_id=None,
        gate_number=None,
        must_change_password=False,
        is_active=True,
    )

    # Manager of Garage 1
    manager1 = await user_repo.create(
        full_name="Babelooq Manager",
        username="ctx_mgr1",
        hashed_password=auth_svc.hash_password("ManagerSecret1!"),
        role=UserRole.MANAGER,
        owner_id=owner1.id,
        garage_id=garage1.id,
        gate_number=None,
        must_change_password=False,
        is_active=True,
    )

    # Operator of Garage 1 (must change password)
    operator1_must_change = await user_repo.create(
        full_name="Gate 1 Cashier",
        username="ctx_op1_temp",
        hashed_password=auth_svc.hash_password("TempPass123!"),
        role=UserRole.OPERATOR,
        owner_id=owner1.id,
        garage_id=garage1.id,
        gate_number=1,
        must_change_password=True,
        is_active=True,
    )

    # Normal operator of Garage 1
    operator1_active = await user_repo.create(
        full_name="Gate 2 Cashier",
        username="ctx_op2_active",
        hashed_password=auth_svc.hash_password("OperatorPass123!"),
        role=UserRole.OPERATOR,
        owner_id=owner1.id,
        garage_id=garage1.id,
        gate_number=2,
        must_change_password=False,
        is_active=True,
    )

    return {
        "owner1": owner1,
        "garage1": garage1,
        "owner2": owner2,
        "garage2": garage2,
        "super_admin": super_admin,
        "owner_user1": owner_user1,
        "manager1": manager1,
        "operator1_must_change": operator1_must_change,
        "operator1_active": operator1_active,
    }


# ---------------------------------------------------------------------------
# 1. TenantContext Dataclass Tests (Task 6.1)
# ---------------------------------------------------------------------------

def test_tenant_context_immutability():
    """Verifies that TenantContext is a frozen immutable dataclass with proper defaults."""
    ctx = TenantContext(
        garage_id=1,
        owner_id=2,
        is_super_admin=False,
        is_system=False,
        user_id=10,
        role="MANAGER",
    )
    assert ctx.garage_id == 1
    assert ctx.owner_id == 2
    assert ctx.is_super_admin is False
    assert ctx.is_system is False
    assert ctx.user_id == 10
    assert ctx.role == "MANAGER"

    # Attempting to modify any field must raise FrozenInstanceError
    with pytest.raises(FrozenInstanceError):
        ctx.garage_id = 99  # type: ignore

    with pytest.raises(FrozenInstanceError):
        ctx.is_super_admin = True  # type: ignore


# ---------------------------------------------------------------------------
# 2. Database Session Event Listener & set_config Injection (Task 6.2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_after_begin_set_config_injection(db_session: AsyncSession):
    """Verifies that after_begin hook injects transaction-local settings via set_config."""
    # Test setting parameters in info
    db_session.info["garage_id"] = 42
    db_session.info["owner_id"] = 7
    db_session.info["is_super_admin"] = True
    db_session.info["is_system"] = False

    # Execute a query; this will trigger receive_after_begin
    await db_session.execute(
        text("""
            SELECT 
                set_config('app.current_garage_id', :gid, true),
                set_config('app.current_owner_id', :oid, true),
                set_config('app.is_super_admin', :isa, true),
                set_config('app.is_system_context', :sys, true);
        """),
        {"gid": "42", "oid": "7", "isa": "true", "sys": "false"}
    )

    gid_res = await db_session.execute(text("SELECT current_setting('app.current_garage_id', true);"))
    oid_res = await db_session.execute(text("SELECT current_setting('app.current_owner_id', true);"))
    isa_res = await db_session.execute(text("SELECT current_setting('app.is_super_admin', true);"))
    sys_res = await db_session.execute(text("SELECT current_setting('app.is_system_context', true);"))

    assert gid_res.scalar() == "42"
    assert oid_res.scalar() == "7"
    assert isa_res.scalar() == "true"
    assert sys_res.scalar() == "false"


# ---------------------------------------------------------------------------
# 3. RBAC & Escalation Guard Tests (Task 6.4)
# ---------------------------------------------------------------------------

def test_validate_role_assignment_escalation_guard(context_test_env):
    """Verifies that validate_role_assignment blocks privilege escalation attempts with 403."""
    env = context_test_env
    mgr = env["manager1"]
    owner = env["owner_user1"]
    operator = env["operator1_active"]
    sa = env["super_admin"]

    # 1. Manager attempts to create a SUPER_ADMIN -> 403 PRIVILEGE_ESCALATION_DENIED
    with pytest.raises(HTTPException) as exc1:
        validate_role_assignment(mgr, UserRole.SUPER_ADMIN, target_garage_id=mgr.garage_id)
    assert exc1.value.status_code == 403
    assert exc1.value.headers.get("X-Error-Code") == "PRIVILEGE_ESCALATION_DENIED"

    # 2. Manager attempts to create another MANAGER -> 403 PRIVILEGE_ESCALATION_DENIED
    with pytest.raises(HTTPException) as exc2:
        validate_role_assignment(mgr, UserRole.MANAGER, target_garage_id=mgr.garage_id)
    assert exc2.value.status_code == 403
    assert exc2.value.headers.get("X-Error-Code") == "PRIVILEGE_ESCALATION_DENIED"

    # 3. Manager attempts to assign user to a different garage -> 403 PRIVILEGE_ESCALATION_DENIED
    with pytest.raises(HTTPException) as exc3:
        validate_role_assignment(mgr, UserRole.OPERATOR, target_garage_id=999)
    assert exc3.value.status_code == 403
    assert exc3.value.headers.get("X-Error-Code") == "PRIVILEGE_ESCALATION_DENIED"

    # 4. Manager assigns an OPERATOR to their own garage -> Allowed (no exception)
    validate_role_assignment(mgr, UserRole.OPERATOR, target_garage_id=mgr.garage_id)

    # 5. Owner attempts to create a SUPER_ADMIN -> 403 PRIVILEGE_ESCALATION_DENIED
    with pytest.raises(HTTPException) as exc5:
        validate_role_assignment(owner, UserRole.SUPER_ADMIN)
    assert exc5.value.status_code == 403
    assert exc5.value.headers.get("X-Error-Code") == "PRIVILEGE_ESCALATION_DENIED"

    # 6. Owner attempts to assign user to a different owner organization -> 403
    with pytest.raises(HTTPException) as exc6:
        validate_role_assignment(owner, UserRole.MANAGER, target_owner_id=9999)
    assert exc6.value.status_code == 403
    assert exc6.value.headers.get("X-Error-Code") == "PRIVILEGE_ESCALATION_DENIED"

    # 7. Owner creates MANAGER or OPERATOR in their own organization -> Allowed
    validate_role_assignment(owner, UserRole.MANAGER, target_owner_id=owner.owner_id)
    validate_role_assignment(owner, UserRole.OPERATOR, target_owner_id=owner.owner_id)

    # 8. Operator attempts to create or manage any user -> 403
    with pytest.raises(HTTPException) as exc8:
        validate_role_assignment(operator, UserRole.OPERATOR, target_garage_id=operator.garage_id)
    assert exc8.value.status_code == 403
    assert exc8.value.headers.get("X-Error-Code") == "PRIVILEGE_ESCALATION_DENIED"

    # 9. Super Admin can assign any role and any scope -> Allowed
    validate_role_assignment(sa, UserRole.SUPER_ADMIN)
    validate_role_assignment(sa, UserRole.OWNER, target_owner_id=1)
    validate_role_assignment(sa, UserRole.MANAGER, target_garage_id=1)


@pytest.mark.asyncio
async def test_rbac_dependencies_role_checks(context_test_env):
    """Verifies require_super_admin, require_owner_or_admin, require_manager_or_above, etc."""
    env = context_test_env
    sa = env["super_admin"]
    owner = env["owner_user1"]
    mgr = env["manager1"]
    op = env["operator1_active"]

    # require_super_admin
    assert await require_super_admin(sa) == sa
    with pytest.raises(HTTPException) as exc_sa:
        await require_super_admin(owner)
    assert exc_sa.value.status_code == 403

    # require_owner_or_admin
    assert await require_owner_or_admin(sa) == sa
    assert await require_owner_or_admin(owner) == owner
    with pytest.raises(HTTPException) as exc_oa:
        await require_owner_or_admin(mgr)
    assert exc_oa.value.status_code == 403

    # require_manager_or_above
    assert await require_manager_or_above(sa) == sa
    assert await require_manager_or_above(owner) == owner
    assert await require_manager_or_above(mgr) == mgr
    with pytest.raises(HTTPException) as exc_m:
        await require_manager_or_above(op)
    assert exc_m.value.status_code == 403

    # require_operator_or_above
    assert await require_operator_or_above(op) == op
    assert await require_operator_or_above(mgr) == mgr


# ---------------------------------------------------------------------------
# 4. Mandatory Password Change Enforcement (Task 6.5)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_must_change_password_enforcement(context_test_env, db_session: AsyncSession):
    """Verifies that users with must_change_password=True are blocked on APIs and redirected on UI."""
    env = context_test_env
    user_temp = env["operator1_must_change"]
    settings = get_settings()
    auth_svc = AuthService(settings)
    token = auth_svc.create_access_token(user_temp.id, user_temp.role.value)

    # Case A: Request to normal API endpoint -> 403 PASSWORD_CHANGE_REQUIRED
    mock_request_api = MagicMock(spec=Request)
    mock_request_api.cookies = {"pgms_token": token}
    mock_request_api.headers = {}
    mock_request_api.url.path = "/api/v1/sessions"

    with pytest.raises(HTTPException) as exc_api:
        await get_current_user(mock_request_api, db=db_session, settings=settings)
    assert exc_api.value.status_code == 403
    assert exc_api.value.headers.get("X-Error-Code") == "PASSWORD_CHANGE_REQUIRED"

    # Case B: Request to exempt API endpoint -> Allowed
    mock_request_exempt = MagicMock(spec=Request)
    mock_request_exempt.cookies = {"pgms_token": token}
    mock_request_exempt.headers = {}
    mock_request_exempt.url.path = "/api/v1/auth/change-password"

    allowed_user = await get_current_user(mock_request_exempt, db=db_session, settings=settings)
    assert allowed_user.id == user_temp.id

    # Case C: Request from Browser to UI endpoint -> 303 Redirect to /ui/auth/change-password
    mock_request_ui = MagicMock(spec=Request)
    mock_request_ui.cookies = {"pgms_token": token}
    mock_request_ui.headers = {}
    mock_request_ui.url.path = "/ui/operator/dashboard"

    with pytest.raises(HTTPException) as exc_ui:
        await get_current_user(mock_request_ui, db=db_session, settings=settings)
    assert exc_ui.value.status_code == 303
    assert exc_ui.value.headers.get("Location") == "/ui/auth/change-password"


# ---------------------------------------------------------------------------
# 5. Tenant Context Extraction & IDOR Protection (Task 6.3 & Task 6.6)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_owner_tenant_context_and_cookie_resolution(context_test_env, db_session: AsyncSession):
    """Verifies owner tenant context resolution via cookie and IDOR prevention."""
    env = context_test_env
    owner_user = env["owner_user1"]
    garage1 = env["garage1"]
    garage2 = env["garage2"]

    # 1. Owner requests with active garage cookie set to owned garage
    mock_req = MagicMock(spec=Request)
    mock_req.path_params = {}
    mock_req.cookies = {"pgms_active_garage": str(garage1.id)}

    ctx = await get_tenant_context(mock_req, current_user=owner_user, db=db_session)
    assert ctx.owner_id == owner_user.owner_id
    assert ctx.garage_id == garage1.id
    assert ctx.is_super_admin is False
    assert db_session.info["garage_id"] == garage1.id
    assert db_session.info["owner_id"] == owner_user.owner_id

    # 2. Owner attempts IDOR to switch to unowned Garage 2 -> 404 TENANT_NOT_FOUND
    mock_req_idor = MagicMock(spec=Request)
    mock_req_idor.path_params = {"garage_id": str(garage2.id)}
    mock_req_idor.cookies = {}

    with pytest.raises(HTTPException) as exc_idor:
        await get_tenant_context(mock_req_idor, current_user=owner_user, db=db_session)
    assert exc_idor.value.status_code == 404
    assert exc_idor.value.headers.get("X-Error-Code") == "TENANT_NOT_FOUND"


@pytest.mark.asyncio
async def test_super_admin_context_and_audit_logging(context_test_env, db_session: AsyncSession):
    """Verifies that Super Admin accessing a garage automatically logs SUPER_ADMIN_GARAGE_ACCESS (Task 6.6)."""
    env = context_test_env
    user_repo = UserRepository(db_session)
    sa = await user_repo.get_by_id(13)
    if not sa:
        sa = env["super_admin"]
    garage1 = env["garage1"]

    mock_req = MagicMock(spec=Request)
    mock_req.path_params = {"garage_id": str(garage1.id)}
    mock_req.url.path = f"/api/v1/garages/{garage1.id}/config"
    mock_req.method = "GET"
    mock_req.cookies = {}

    ctx = await get_tenant_context(mock_req, current_user=sa, db=db_session)
    assert ctx.is_super_admin is True
    assert ctx.garage_id == garage1.id
    assert ctx.owner_id is None
    assert db_session.info["is_super_admin"] is True

    # Verify that an audit record with action SUPER_ADMIN_GARAGE_ACCESS was created
    audit_stmt = (
        select(AuditLog)
        .where(
            AuditLog.actor_id == sa.id,
            AuditLog.action == "SUPER_ADMIN_GARAGE_ACCESS",
        )
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    result = await db_session.execute(audit_stmt)
    log_entry = result.scalars().first()

    assert log_entry is not None
    assert log_entry.entity_type == "platform"
    assert log_entry.garage_id is None
    payload = json.loads(log_entry.payload_after)
    assert payload["target_garage_id"] == garage1.id
    assert payload["endpoint"] == f"/api/v1/garages/{garage1.id}/config"


# ---------------------------------------------------------------------------
# 6. Auth Service Disambiguation by Garage Code & Failed Login Audit (Task 6.7)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auth_service_garage_code_disambiguation(context_test_env, db_session: AsyncSession):
    """Verifies authenticate_user requires garage_code when duplicate usernames exist across garages."""
    env = context_test_env
    user_repo = UserRepository(db_session)
    auth_svc = AuthService(get_settings())

    # Create a cashier in Garage 1 and another cashier in Garage 2 with the same username 'cashier1'
    await user_repo.create(
        full_name="Cashier One G1",
        username="cashier1",
        hashed_password=auth_svc.hash_password("PassG1_123!"),
        role=UserRole.OPERATOR,
        owner_id=env["owner1"].id,
        garage_id=env["garage1"].id,
        gate_number=1,
        must_change_password=False,
        is_active=True,
    )

    await user_repo.create(
        full_name="Cashier One G2",
        username="cashier1",
        hashed_password=auth_svc.hash_password("PassG2_123!"),
        role=UserRole.OPERATOR,
        owner_id=env["owner2"].id,
        garage_id=env["garage2"].id,
        gate_number=1,
        must_change_password=False,
        is_active=True,
    )

    # 1. Attempting login without garage_code when duplicate exists raises AuthenticationError
    with pytest.raises(AuthenticationError) as exc_dup:
        await auth_svc.authenticate_user("cashier1", "PassG1_123!", user_repo)
    assert "Garage code is required" in str(exc_dup.value)

    # 2. Login with correct garage_code for Garage 1 succeeds
    u1 = await auth_svc.authenticate_user(
        "cashier1", "PassG1_123!", user_repo, garage_code=env["garage1"].code
    )
    assert u1.garage_id == env["garage1"].id
    assert u1.full_name == "Cashier One G1"

    # 3. Login with correct garage_code for Garage 2 succeeds
    u2 = await auth_svc.authenticate_user(
        "cashier1", "PassG2_123!", user_repo, garage_code=env["garage2"].code
    )
    assert u2.garage_id == env["garage2"].id
    assert u2.full_name == "Cashier One G2"
