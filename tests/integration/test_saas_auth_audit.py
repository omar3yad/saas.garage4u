import pytest
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import FastAPI

from config import get_settings
from database import get_db
from models.owner import Owner
from models.garage import Garage
from models.garage_config import GarageConfig
from models.user import User, UserRole
from models.audit_log import AuditLog
from services.auth_service import AuthService
from routes.auth import router as auth_router
from routes.users import router as users_router
from routes.garage_management_api import router as garage_mgmt_router


@pytest.fixture
def test_app(pg_migrator_session: AsyncSession):
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(garage_mgmt_router)
    app.dependency_overrides[get_db] = lambda: pg_migrator_session
    return app


@pytest.fixture
async def auth_audit_env(pg_migrator_session: AsyncSession):
    auth_svc = AuthService(get_settings())

    owner = Owner(
        name="Audit Group",
        company_name="Audit Corp",
        contact_phone="+201099887766",
        contact_email="audit@tenant.test",
        billing_status="ACTIVE",
        max_garages_allowed=3,
    )
    pg_migrator_session.add(owner)
    await pg_migrator_session.flush()

    garage = Garage(
        owner_id=owner.id,
        name="Audit Garage",
        code="GAR-AUDIT",
        status="ACTIVE",
        capacity=100,
        can_use_anpr=True,
        can_use_subscriptions=True,
        max_gates=2,
    )
    pg_migrator_session.add(garage)
    await pg_migrator_session.flush()

    garage_cfg = GarageConfig(garage_id=garage.id)
    pg_migrator_session.add(garage_cfg)
    await pg_migrator_session.flush()

    super_admin = User(
        username="sa_audit",
        full_name="Super Admin Audit",
        role=UserRole.SUPER_ADMIN,
        hashed_password=auth_svc.hash_password("SuperPass123!"),
        is_active=True,
    )
    manager = User(
        username="mgr_audit",
        full_name="Manager Audit",
        role=UserRole.MANAGER,
        owner_id=owner.id,
        garage_id=garage.id,
        hashed_password=auth_svc.hash_password("MgrPass123!"),
        is_active=True,
    )
    pwd_user = User(
        username="pwd_must_change",
        full_name="Must Change User",
        role=UserRole.OPERATOR,
        owner_id=owner.id,
        garage_id=garage.id,
        gate_number=1,
        hashed_password=auth_svc.hash_password("TempPass123!"),
        must_change_password=True,
        is_active=True,
    )
    pg_migrator_session.add_all([super_admin, manager, pwd_user])
    await pg_migrator_session.commit()

    yield {
        "owner": owner,
        "garage": garage,
        "super_admin": super_admin,
        "manager": manager,
        "pwd_user": pwd_user,
        "sa_token": auth_svc.create_access_token(super_admin.id, super_admin.role.value),
        "mgr_token": auth_svc.create_access_token(manager.id, manager.role.value),
        "pwd_token": auth_svc.create_access_token(pwd_user.id, pwd_user.role.value),
    }

    # Cleanup
    try:
        await pg_migrator_session.execute(text(f"DELETE FROM audit_logs WHERE actor_id IN ({super_admin.id}, {manager.id}, {pwd_user.id});"))
        await pg_migrator_session.execute(text(f"DELETE FROM users WHERE id IN ({super_admin.id}, {manager.id}, {pwd_user.id});"))
        await pg_migrator_session.execute(text(f"DELETE FROM garage_configs WHERE garage_id = {garage.id};"))
        await pg_migrator_session.execute(text(f"DELETE FROM garages WHERE id = {garage.id};"))
        await pg_migrator_session.execute(text(f"DELETE FROM owners WHERE id = {owner.id};"))
        await pg_migrator_session.commit()
    except Exception:
        await pg_migrator_session.rollback()


def _auth(token: str):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_must_change_password_blocks_operations(test_app: FastAPI, auth_audit_env: dict):
    """Verifies that must_change_password=True blocks API requests with 403 PASSWORD_CHANGE_REQUIRED."""
    transport = ASGITransport(app=test_app)
    headers = _auth(auth_audit_env["pwd_token"])

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Operational request -> Blocked with 403
        res = await client.get("/api/v1/users/", headers=headers)
        assert res.status_code == 403
        assert res.headers.get("X-Error-Code") == "PASSWORD_CHANGE_REQUIRED"

        # 2. Change password request -> Permitted
        res_change = await client.post(
            "/api/v1/auth/change-password",
            headers=headers,
            json={"current_password": "TempPass123!", "new_password": "BrandNewPass123!"},
        )
        assert res_change.status_code in (200, 204)


@pytest.mark.asyncio
async def test_privilege_escalation_denied(test_app: FastAPI, auth_audit_env: dict):
    """Verifies that a Manager attempting to create a user with Manager/Owner/SuperAdmin role is denied."""
    transport = ASGITransport(app=test_app)
    headers = _auth(auth_audit_env["mgr_token"])

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Attempt to create another MANAGER
        res = await client.post(
            "/api/v1/users/",
            headers=headers,
            json={
                "username": "rogue_mgr",
                "full_name": "Rogue Manager",
                "role": "MANAGER",
                "password": "Password123!",
            },
        )
        assert res.status_code == 403
        assert res.headers.get("X-Error-Code") == "PRIVILEGE_ESCALATION_DENIED"


@pytest.mark.asyncio
async def test_super_admin_garage_access_audit_logged(test_app: FastAPI, auth_audit_env: dict, pg_migrator_session: AsyncSession):
    """Verifies that Super Admin accessing a scoped garage endpoint records SUPER_ADMIN_GARAGE_ACCESS in audit_logs."""
    transport = ASGITransport(app=test_app)
    headers = _auth(auth_audit_env["sa_token"])
    gid = auth_audit_env["garage"].id

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(f"/api/v1/garages/{gid}/config", headers=headers)
        assert res.status_code == 200

    # Query audit logs
    res_logs = await pg_migrator_session.execute(
        select(AuditLog).where(AuditLog.action == "SUPER_ADMIN_GARAGE_ACCESS")
    )
    logs = res_logs.scalars().all()
    assert len(logs) >= 1
    assert any("config" in (log.payload_after or "") for log in logs)
