import os
import io
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, WebSocket, status
from httpx import AsyncClient, ASGITransport
from starlette.testclient import TestClient
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from models.owner import Owner
from models.garage import Garage
from models.garage_gate import GarageGate
from models.garage_config import GarageConfig
from models.user import User, UserRole
from models.shift import Shift
from models.parking_session import ParkingSession, SessionStatus
from models.edge_device import EdgeDevice, EdgeDeviceGate
from repositories.edge_device_repo import EdgeDeviceRepository
from routes.edge_api import router as edge_api_router
from routes.edge_ws import router as edge_ws_router, _authenticate_edge_device
from services.auth_service import AuthService


@pytest.fixture
def edge_app(pg_migrator_session: AsyncSession):
    app = FastAPI()
    app.include_router(edge_api_router)
    app.include_router(edge_ws_router)
    app.dependency_overrides[get_db] = lambda: pg_migrator_session
    app.state.db = pg_migrator_session
    return app


@pytest.fixture
async def edge_test_env(pg_migrator_session: AsyncSession):
    session = pg_migrator_session
    auth_svc = AuthService(get_settings())

    # 1. Two Garages (Garage A: ANPR enabled, Garage B: ANPR disabled)
    owner = Owner(
        name="Edge Multi-Tenant Owner",
        company_name="Edge Corp",
        contact_phone="+201011119999",
        contact_email="edge_mt@test.com",
        billing_status="ACTIVE",
        max_garages_allowed=2,
    )
    session.add(owner)
    await session.flush()

    garage_a = Garage(
        owner_id=owner.id,
        name="Garage Edge Alpha",
        code="GAR-EDGE-A",
        status="ACTIVE",
        capacity=100,
        can_use_anpr=True,
        can_use_subscriptions=True,
        max_gates=4,
    )
    garage_b = Garage(
        owner_id=owner.id,
        name="Garage Edge Beta",
        code="GAR-EDGE-B",
        status="ACTIVE",
        capacity=100,
        can_use_anpr=False,
        can_use_subscriptions=True,
        max_gates=4,
    )
    session.add_all([garage_a, garage_b])
    await session.flush()

    # Configs
    cfg_a = GarageConfig(garage_id=garage_a.id, anpr_enabled=True)
    cfg_b = GarageConfig(garage_id=garage_b.id, anpr_enabled=False)
    session.add_all([cfg_a, cfg_b])

    # Gates (Gate 1 in Garage A, Gate 1 in Garage B)
    gate_a1 = GarageGate(garage_id=garage_a.id, gate_number=1, name="Alpha Entry 1", gate_type="ENTRY")
    gate_a2 = GarageGate(garage_id=garage_a.id, gate_number=2, name="Alpha Entry 2", gate_type="ENTRY")
    gate_b1 = GarageGate(garage_id=garage_b.id, gate_number=1, name="Beta Entry 1", gate_type="ENTRY")
    session.add_all([gate_a1, gate_a2, gate_b1])
    await session.flush()

    # Operator for Garage A
    op_a = User(
        username="op_edge_a",
        full_name="Operator Edge A",
        role=UserRole.OPERATOR,
        owner_id=owner.id,
        garage_id=garage_a.id,
        gate_number=1,
        hashed_password=auth_svc.hash_password("Pass123!"),
        is_active=True,
    )
    op_b = User(
        username="op_edge_b",
        full_name="Operator Edge B",
        role=UserRole.OPERATOR,
        owner_id=owner.id,
        garage_id=garage_b.id,
        gate_number=1,
        hashed_password=auth_svc.hash_password("Pass123!"),
        is_active=True,
    )
    session.add_all([op_a, op_b])
    await session.flush()

    # Shifts
    now = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
    shift_a = Shift(garage_id=garage_a.id, operator_id=op_a.id, gate_number=1, started_at=now)
    shift_b = Shift(garage_id=garage_b.id, operator_id=op_b.id, gate_number=1, started_at=now)
    session.add_all([shift_a, shift_b])
    await session.flush()

    # Parking Sessions
    sess_a = ParkingSession(
        garage_id=garage_a.id,
        receipt_no=2001,
        ticket_code="TKT-EDGE-A1",
        shift_id=shift_a.id,
        operator_id=op_a.id,
        gate_number=1,
        entry_time=now,
        status=SessionStatus.ACTIVE,
    )
    sess_b = ParkingSession(
        garage_id=garage_b.id,
        receipt_no=2001,
        ticket_code="TKT-EDGE-B1",
        shift_id=shift_b.id,
        operator_id=op_b.id,
        gate_number=1,
        entry_time=now,
        status=SessionStatus.ACTIVE,
    )
    session.add_all([sess_a, sess_b])
    await session.flush()

    # Edge Devices
    # 1. Active Camera on Garage A assigned ONLY to Gate 1
    raw_token_a = f"edg_{secrets.token_hex(24)}"
    token_hash_a = hashlib.sha256(raw_token_a.encode()).hexdigest()
    dev_a = EdgeDevice(
        garage_id=garage_a.id,
        device_name="Camera Alpha 1",
        token_hash=token_hash_a,
    )
    # 2. Revoked Camera on Garage A
    raw_token_revoked = f"edg_{secrets.token_hex(24)}"
    token_hash_revoked = hashlib.sha256(raw_token_revoked.encode()).hexdigest()
    dev_revoked = EdgeDevice(
        garage_id=garage_a.id,
        device_name="Revoked Camera Alpha",
        token_hash=token_hash_revoked,
        revoked_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add_all([dev_a, dev_revoked])
    await session.flush()

    # Assign dev_a ONLY to Gate 1
    edg_gate_a1 = EdgeDeviceGate(
        device_id=dev_a.id,
        garage_id=garage_a.id,
        gate_number=1,
        is_active=True,
    )
    session.add(edg_gate_a1)
    await session.commit()

    yield {
        "garage_a": garage_a,
        "garage_b": garage_b,
        "dev_a": dev_a,
        "token_a": raw_token_a,
        "dev_revoked": dev_revoked,
        "token_revoked": raw_token_revoked,
        "session_a": sess_a,
        "session_b": sess_b,
    }

    # Teardown
    try:
        await session.execute(text(f"DELETE FROM edge_device_gates WHERE garage_id IN ({garage_a.id}, {garage_b.id});"))
        await session.execute(text(f"DELETE FROM edge_devices WHERE garage_id IN ({garage_a.id}, {garage_b.id});"))
        await session.execute(text(f"DELETE FROM parking_sessions WHERE garage_id IN ({garage_a.id}, {garage_b.id});"))
        await session.execute(text(f"DELETE FROM shifts WHERE garage_id IN ({garage_a.id}, {garage_b.id});"))
        await session.execute(text(f"DELETE FROM garage_gates WHERE garage_id IN ({garage_a.id}, {garage_b.id});"))
        await session.execute(text(f"DELETE FROM garage_configs WHERE garage_id IN ({garage_a.id}, {garage_b.id});"))
        await session.execute(text(f"DELETE FROM users WHERE garage_id IN ({garage_a.id}, {garage_b.id});"))
        await session.execute(text(f"DELETE FROM garages WHERE id IN ({garage_a.id}, {garage_b.id});"))
        await session.execute(text(f"DELETE FROM owners WHERE id = {owner.id};"))
        await session.commit()
    except Exception:
        await session.rollback()


# ---------------------------------------------------------------------------
# Task 11.7 Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_edge_ws_handshake_global_lookup_success(edge_test_env: dict, pg_migrator_session: AsyncSession):
    """Verifies that WebSocket authentication looks up device globally by token hash without pre-set context."""
    token = edge_test_env["token_a"]

    mock_ws = MagicMock()
    mock_ws.headers = {"authorization": f"Bearer {token}"}
    mock_ws.query_params = {}
    mock_ws.cookies = {}
    mock_ws.app.state.db = pg_migrator_session

    device, is_entitled = await _authenticate_edge_device(mock_ws, db=pg_migrator_session)
    assert device is not None
    assert device.device_name == "Camera Alpha 1"
    assert is_entitled is True


@pytest.mark.asyncio
async def test_edge_ws_handshake_revoked_token_rejected(edge_test_env: dict, pg_migrator_session: AsyncSession):
    """Verifies that a revoked token fails global authentication and returns None / False."""
    revoked_token = edge_test_env["token_revoked"]

    mock_ws = MagicMock()
    mock_ws.headers = {"authorization": f"Bearer {revoked_token}"}
    mock_ws.query_params = {}
    mock_ws.cookies = {}
    mock_ws.app.state.db = pg_migrator_session

    device, is_entitled = await _authenticate_edge_device(mock_ws, db=pg_migrator_session)
    assert device is None
    assert is_entitled is False


@pytest.mark.asyncio
async def test_edge_upload_cross_tenant_session_returns_404(edge_app: FastAPI, edge_test_env: dict):
    """Verifies that device from Garage A attempting to upload photo for session in Garage B returns 404 TENANT_NOT_FOUND."""
    transport = ASGITransport(app=edge_app)
    headers = {"X-Edge-Token": edge_test_env["token_a"]}
    cross_session_id = edge_test_env["session_b"].id

    dummy_image = io.BytesIO(b"\xFF\xD8\xFF\xE0" + b"\x00" * 1024)  # Valid small JPEG header

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/edge/upload-capture",
            headers=headers,
            data={
                "session_id": cross_session_id,
                "gate_number": 1,
                "capture_type": "entry",
            },
            files={"file": ("capture.jpg", dummy_image.getvalue(), "image/jpeg")},
        )
        assert res.status_code == 404
        assert res.headers.get("X-Error-Code") == "TENANT_NOT_FOUND"


@pytest.mark.asyncio
async def test_edge_upload_unassigned_gate_returns_403(edge_app: FastAPI, edge_test_env: dict):
    """Verifies that camera attempting upload for gate not assigned in edge_device_gates returns 403 GATE_NOT_ASSIGNED."""
    transport = ASGITransport(app=edge_app)
    headers = {"X-Edge-Token": edge_test_env["token_a"]}
    own_session_id = edge_test_env["session_a"].id

    dummy_image = io.BytesIO(b"\xFF\xD8\xFF\xE0" + b"\x00" * 1024)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Gate 2 is in Garage A, but dev_a is ONLY assigned to Gate 1
        res = await client.post(
            "/api/v1/edge/upload-capture",
            headers=headers,
            data={
                "session_id": own_session_id,
                "gate_number": 2,  # Unassigned gate
                "capture_type": "entry",
            },
            files={"file": ("capture.jpg", dummy_image.getvalue(), "image/jpeg")},
        )
        assert res.status_code == 403
        assert res.headers.get("X-Error-Code") == "GATE_NOT_ASSIGNED"


@pytest.mark.asyncio
async def test_edge_upload_payload_exceeding_1_5mb_returns_413(edge_app: FastAPI, edge_test_env: dict):
    """Verifies that image file exceeding strict 1.5MB limit returns 413 IMAGE_SIZE_EXCEEDED."""
    transport = ASGITransport(app=edge_app)
    headers = {"X-Edge-Token": edge_test_env["token_a"]}
    own_session_id = edge_test_env["session_a"].id

    # 1.6 MB payload (1,600,000 bytes > 1,500,000)
    oversized_data = b"\x00" * 1_600_000

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/edge/upload-capture",
            headers=headers,
            data={
                "session_id": own_session_id,
                "gate_number": 1,
                "capture_type": "entry",
            },
            files={"file": ("huge.jpg", oversized_data, "image/jpeg")},
        )
        assert res.status_code == 413
        assert res.headers.get("X-Error-Code") == "IMAGE_SIZE_EXCEEDED"
