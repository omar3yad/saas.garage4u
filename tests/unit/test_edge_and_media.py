import os
import time
import hashlib
import secrets
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI, status
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.pool import NullPool

from config import get_settings
from models.user import User, UserRole
from models.owner import Owner
from models.garage import Garage
from models.garage_gate import GarageGate
from models.garage_config import GarageConfig
from models.pricing_rule import PricingRule
from models.parking_card import ParkingCard, CardStatus
from models.parking_session import ParkingSession, SessionStatus
from models.shift import Shift
from models.edge_device import EdgeDevice, EdgeDeviceGate

from repositories.edge_device_repo import EdgeDeviceRepository
from repositories.session_repo import ParkingSessionRepository

from services.storage import LocalStorageProvider, FileTooLargeError
from services.auth_service import AuthService
from utils.tenant_context import TenantContext
from dependencies import get_tenant_context, get_db

from routes.edge_ws import _authenticate_edge_device, edge_socket
from routes.edge_api import router as edge_api_router
from routes.garage_management_api import router as garage_management_api_router
from tasks.media_retention import cleanup_expired_media


# ---------------------------------------------------------------------------
# Database Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_session():
    """Provides an isolated AsyncSession against PostgreSQL rolled back after each test."""
    settings = get_settings()
    db_url = settings.MIGRATION_DATABASE_URL or settings.DATABASE_URL
    engine = create_async_engine(db_url, poolclass=NullPool)
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        await conn.execute(text("SELECT set_config('app.is_system_context', 'true', true);"))
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def edge_test_env(db_session: AsyncSession):
    """Sets up two distinct garages for cross-tenant validation, complete with
    edge devices, assigned gates, active shifts, and sessions.
    """
    # 1. Owner
    owner = Owner(
        name="Media & Edge Owner",
        company_name="Edge Secure Systems",
        contact_phone="+201011111111",
        contact_email="edge_owner@garage4u.com",
        subscription_plan="ENTERPRISE",
        billing_status="ACTIVE",
        max_garages_allowed=5,
    )
    db_session.add(owner)
    await db_session.flush()

    # 2. Garage 1 (Licensed for ANPR)
    garage1 = Garage(
        owner_id=owner.id,
        code="GARAGE-EDGE-01",
        name="Garage Edge Alpha",
        city="Cairo",
        capacity=100,
        status="ACTIVE",
        can_use_anpr=True,
        can_use_subscriptions=True,
        max_gates=4,
    )
    db_session.add(garage1)

    # 3. Garage 2 (For cross-tenant testing)
    garage2 = Garage(
        owner_id=owner.id,
        code="GARAGE-EDGE-02",
        name="Garage Edge Beta",
        city="Giza",
        capacity=50,
        status="ACTIVE",
        can_use_anpr=False,  # Unentitled for ANPR
        can_use_subscriptions=True,
        max_gates=2,
    )
    db_session.add(garage2)
    await db_session.flush()

    # 4. Gates for Garage 1 & Garage 2
    gate1_g1 = GarageGate(garage_id=garage1.id, gate_number=1, name="G1 Main Entry", gate_type="ENTRY")
    gate2_g1 = GarageGate(garage_id=garage1.id, gate_number=2, name="G1 Main Exit", gate_type="EXIT")
    gate1_g2 = GarageGate(garage_id=garage2.id, gate_number=1, name="G2 Solo Gate", gate_type="MANUAL_BIDIRECTIONAL")
    db_session.add_all([gate1_g1, gate2_g1, gate1_g2])
    await db_session.flush()

    # 5. Garage Configs
    cfg1 = GarageConfig(
        garage_id=garage1.id,
        ticket_system="REUSABLE_CARDS",
        anpr_enabled=True,
        exit_photo_enabled=True,
        version=1,
    )
    cfg2 = GarageConfig(
        garage_id=garage2.id,
        ticket_system="REUSABLE_CARDS",
        anpr_enabled=False,
        version=1,
    )
    db_session.add_all([cfg1, cfg2])
    await db_session.flush()

    # 6. Edge Devices
    raw_token_1 = "edge_secret_token_alpha_123456789012345"
    token_hash_1 = hashlib.sha256(raw_token_1.encode()).hexdigest()
    device1 = EdgeDevice(
        garage_id=garage1.id,
        device_name="Cam-G1-PC",
        token_hash=token_hash_1,
    )

    raw_token_revoked = "edge_secret_token_revoked_9876543210987"
    token_hash_revoked = hashlib.sha256(raw_token_revoked.encode()).hexdigest()
    device_revoked = EdgeDevice(
        garage_id=garage1.id,
        device_name="Cam-G1-Revoked",
        token_hash=token_hash_revoked,
        revoked_at=datetime.utcnow(),
    )

    raw_token_2 = "edge_secret_token_beta_55555555555555555"
    token_hash_2 = hashlib.sha256(raw_token_2.encode()).hexdigest()
    device2 = EdgeDevice(
        garage_id=garage2.id,
        device_name="Cam-G2-PC",
        token_hash=token_hash_2,
    )

    db_session.add_all([device1, device_revoked, device2])
    await db_session.flush()

    # 7. Assign Gate 1 to Device 1 (Gate 2 is deliberately unassigned to Device 1)
    dev_gate = EdgeDeviceGate(
        device_id=device1.id,
        garage_id=garage1.id,
        gate_number=1,
        direction="ENTRY",
        is_active=True,
    )
    db_session.add(dev_gate)
    await db_session.flush()

    # 8. Operators for both garages
    auth_svc = AuthService(get_settings())
    op1 = User(
        username=f"op1_{garage1.id}",
        full_name="Operator Garage 1",
        role=UserRole.OPERATOR,
        garage_id=garage1.id,
        owner_id=owner.id,
        gate_number=1,
        hashed_password=auth_svc.hash_password("OpPass123!"),
        is_active=True,
    )
    op2 = User(
        username=f"op2_{garage2.id}",
        full_name="Operator Garage 2",
        role=UserRole.OPERATOR,
        garage_id=garage2.id,
        owner_id=owner.id,
        gate_number=1,
        hashed_password=auth_svc.hash_password("OpPass123!"),
        is_active=True,
    )
    db_session.add_all([op1, op2])
    await db_session.flush()

    # 9. Shifts
    shift1 = Shift(
        garage_id=garage1.id,
        operator_id=op1.id,
        gate_number=1,
        started_at=datetime.utcnow() - timedelta(hours=1),
        opening_cash_egp=100,
    )
    shift2 = Shift(
        garage_id=garage2.id,
        operator_id=op2.id,
        gate_number=1,
        started_at=datetime.utcnow() - timedelta(hours=1),
        opening_cash_egp=100,
    )
    db_session.add_all([shift1, shift2])
    await db_session.flush()

    # 10. Sessions
    session1 = ParkingSession(
        garage_id=garage1.id,
        receipt_no=1001,
        ticket_code="TK-G1-001",
        gate_number=1,
        shift_id=shift1.id,
        operator_id=op1.id,
        entry_time=datetime.utcnow() - timedelta(minutes=30),
        status=SessionStatus.ACTIVE,
    )
    session2 = ParkingSession(
        garage_id=garage2.id,
        receipt_no=2001,
        ticket_code="TK-G2-001",
        gate_number=1,
        shift_id=shift2.id,
        operator_id=op2.id,
        entry_time=datetime.utcnow() - timedelta(minutes=10),
        status=SessionStatus.ACTIVE,
    )
    db_session.add_all([session1, session2])
    await db_session.flush()

    return {
        "owner": owner,
        "garage1": garage1,
        "garage2": garage2,
        "device1": device1,
        "raw_token_1": raw_token_1,
        "device_revoked": device_revoked,
        "raw_token_revoked": raw_token_revoked,
        "device2": device2,
        "raw_token_2": raw_token_2,
        "op1": op1,
        "op2": op2,
        "shift1": shift1,
        "shift2": shift2,
        "session1": session1,
        "session2": session2,
    }


# ===========================================================================
# Task 8.1: Storage Layer Tests (Size limit & path structure)
# ===========================================================================

@pytest.mark.asyncio
async def test_storage_provider_isolated_path_and_file_operations(tmp_path):
    """Verifies image saving under isolated multi-tenant path and file operations."""
    provider = LocalStorageProvider(base_dir=str(tmp_path), max_bytes=1_500_000)
    garage_id = 42
    session_id = 999
    now = datetime(2026, 10, 31, 12, 0, 0)
    fake_image_bytes = b"RIFF....WEBPVP8 ... fake plate image data"

    # 1. Save entry image
    rel_path = await provider.save_image(
        garage_id=garage_id,
        session_id=session_id,
        image_type="entry",
        data=fake_image_bytes,
        timestamp=now,
    )

    expected_path = f"media/garages/{garage_id}/plates/2026/10/{session_id}_entry.webp"
    assert rel_path == expected_path

    # Verify physical file existence
    abs_path = await provider.get_image_path(rel_path)
    assert abs_path is not None
    assert os.path.exists(abs_path)
    with open(abs_path, "rb") as f:
        assert f.read() == fake_image_bytes

    # 2. Delete image
    deleted = await provider.delete_image(rel_path)
    assert deleted is True
    assert not os.path.exists(abs_path)
    assert await provider.get_image_path(rel_path) is None


@pytest.mark.asyncio
async def test_storage_provider_rejects_oversized_image(tmp_path):
    """Verifies strict 1,500,000 bytes maximum limit enforcement."""
    provider = LocalStorageProvider(base_dir=str(tmp_path), max_bytes=1_500_000)
    oversized_data = b"X" * 1_500_001

    with pytest.raises(FileTooLargeError) as exc_info:
        await provider.save_image(
            garage_id=1,
            session_id=1,
            image_type="entry",
            data=oversized_data,
        )
    assert "exceeds maximum permitted limit" in str(exc_info.value)
    assert exc_info.value.size == 1_500_001


# ===========================================================================
# Task 8.2: Edge WebSocket Handshake & Auth Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_edge_websocket_authentication_scenarios(db_session: AsyncSession, edge_test_env: dict):
    """Verifies edge authentication: valid tokens, revoked tokens, and ANPR license checks."""
    raw_token_1 = edge_test_env["raw_token_1"]
    raw_token_revoked = edge_test_env["raw_token_revoked"]
    raw_token_2 = edge_test_env["raw_token_2"]  # Garage 2 has can_use_anpr = False

    # 1. Valid token and entitled garage
    mock_ws_valid = MagicMock()
    mock_ws_valid.headers = {"x-edge-token": raw_token_1}
    mock_ws_valid.query_params = {}

    device, is_entitled = await _authenticate_edge_device(mock_ws_valid, db=db_session)
    assert device is not None
    assert device.device_name == "Cam-G1-PC"
    assert is_entitled is True

    # 2. Revoked token
    mock_ws_revoked = MagicMock()
    mock_ws_revoked.headers = {"x-edge-token": raw_token_revoked}
    mock_ws_revoked.query_params = {}

    dev_rev, ent_rev = await _authenticate_edge_device(mock_ws_revoked, db=db_session)
    assert dev_rev is None
    assert ent_rev is False

    # 3. Invalid/unknown token
    mock_ws_unknown = MagicMock()
    mock_ws_unknown.headers = {"x-edge-token": "totally_fabricated_token_value_xyz"}
    mock_ws_unknown.query_params = {}

    dev_unk, ent_unk = await _authenticate_edge_device(mock_ws_unknown, db=db_session)
    assert dev_unk is None
    assert ent_unk is False

    # 4. Valid token BUT garage is not entitled to ANPR (can_use_anpr=False)
    mock_ws_unentitled = MagicMock()
    mock_ws_unentitled.headers = {"x-edge-token": raw_token_2}
    mock_ws_unentitled.query_params = {}

    dev_unent, ent_unent = await _authenticate_edge_device(mock_ws_unentitled, db=db_session)
    assert dev_unent is not None
    assert ent_unent is False  # Refused ANPR license!


@pytest.mark.asyncio
async def test_edge_websocket_close_with_1008_on_unauthorized(db_session: AsyncSession):
    """Verifies edge_socket closes connection with 1008 Policy Violation on failed auth."""
    mock_ws = AsyncMock()
    mock_ws.headers = {"x-edge-token": "bad_token"}
    mock_ws.query_params = {}
    mock_ws.app.state.edge_hub = MagicMock()
    mock_ws.app.state.db = db_session

    await edge_socket(mock_ws)
    mock_ws.close.assert_awaited_once_with(
        code=status.WS_1008_POLICY_VIOLATION,
        reason="Policy Violation: Authentication or ANPR license required",
    )


# ===========================================================================
# Task 8.3: Authenticated Edge Image Upload Route Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_edge_upload_capture_api_endpoints(db_session: AsyncSession, edge_test_env: dict):
    """Verifies edge image upload API: token auth, RLS scoping, gate assignment, and file size checks."""
    device1 = edge_test_env["device1"]
    raw_token_1 = edge_test_env["raw_token_1"]
    session1 = edge_test_env["session1"]
    session2 = edge_test_env["session2"]

    # Setup test FastAPI app
    app = FastAPI()
    app.include_router(edge_api_router)
    app.state.edge_hub = MagicMock()
    app.dependency_overrides[get_db] = lambda: db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Missing X-Edge-Token -> 401
        res_no_token = await client.post(
            "/api/v1/edge/upload-capture",
            data={"session_id": session1.id, "gate_number": 1, "capture_type": "entry"},
            files={"file": ("plate.webp", b"WEBP...", "image/webp")},
        )
        assert res_no_token.status_code == 401
        assert res_no_token.headers.get("X-Error-Code") == "EDGE_AUTH_FAILED"

        # 2. Invalid X-Edge-Token -> 401
        res_bad_token = await client.post(
            "/api/v1/edge/upload-capture",
            headers={"X-Edge-Token": "invalid_token_xyz"},
            data={"session_id": session1.id, "gate_number": 1, "capture_type": "entry"},
            files={"file": ("plate.webp", b"WEBP...", "image/webp")},
        )
        assert res_bad_token.status_code == 401
        assert res_bad_token.headers.get("X-Error-Code") == "EDGE_AUTH_FAILED"

        # 3. Gate NOT assigned to this device (Device 1 has gate 1, attempting gate 2) -> 403
        res_wrong_gate = await client.post(
            "/api/v1/edge/upload-capture",
            headers={"X-Edge-Token": raw_token_1},
            data={"session_id": session1.id, "gate_number": 2, "capture_type": "entry"},
            files={"file": ("plate.webp", b"WEBP...", "image/webp")},
        )
        assert res_wrong_gate.status_code == 403
        assert res_wrong_gate.headers.get("X-Error-Code") == "GATE_NOT_ASSIGNED"

        # 4. Session belonging to another garage (cross-tenant attempt on session2) -> 404
        res_cross_tenant = await client.post(
            "/api/v1/edge/upload-capture",
            headers={"X-Edge-Token": raw_token_1},
            data={"session_id": session2.id, "gate_number": 1, "capture_type": "entry"},
            files={"file": ("plate.webp", b"WEBP...", "image/webp")},
        )
        assert res_cross_tenant.status_code == 404
        assert res_cross_tenant.headers.get("X-Error-Code") == "TENANT_NOT_FOUND"

        # 5. Oversized image (> 1.5MB) -> 413
        oversized_bytes = b"O" * 1_500_001
        res_oversized = await client.post(
            "/api/v1/edge/upload-capture",
            headers={"X-Edge-Token": raw_token_1},
            data={"session_id": session1.id, "gate_number": 1, "capture_type": "entry"},
            files={"file": ("plate.webp", oversized_bytes, "image/webp")},
        )
        assert res_oversized.status_code == 413
        assert res_oversized.headers.get("X-Error-Code") == "IMAGE_SIZE_EXCEEDED"

        # 6. Valid Upload -> 201 Created
        valid_img = b"RIFF_WEBP_VALID_IMAGE_DATA_123"
        res_valid = await client.post(
            "/api/v1/edge/upload-capture",
            headers={"X-Edge-Token": raw_token_1},
            data={
                "session_id": session1.id,
                "gate_number": 1,
                "capture_type": "entry",
                "plate_number": "123-ABC",
                "confidence": 0.98,
            },
            files={"file": ("plate.webp", valid_img, "image/webp")},
        )
        assert res_valid.status_code == 201
        body = res_valid.json()
        assert body["status"] == "success"
        assert body["session_id"] == session1.id
        assert "media/garages/" in body["image_path"]

        # Verify session record in DB was updated
        await db_session.refresh(session1)
        assert session1.plate_image_path == body["image_path"]
        assert session1.plate_capture_status == "SUCCESS"
        assert session1.plate_confidence == 0.98
        assert session1.plate_number == "123-ABC"
        assert session1.plate_captured_at is not None


# ===========================================================================
# Task 8.4: Authenticated Media Serving & Cross-Tenant 404 Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_authenticated_plate_image_serving_and_cross_tenant_isolation(
    db_session: AsyncSession, edge_test_env: dict, tmp_path
):
    """Verifies plate-image endpoint serves images to authorized tenant and rejects cross-tenant requests with 404."""
    garage1 = edge_test_env["garage1"]
    garage2 = edge_test_env["garage2"]
    session1 = edge_test_env["session1"]
    op1 = edge_test_env["op1"]
    op2 = edge_test_env["op2"]

    # Write a test file on disk
    provider = LocalStorageProvider(base_dir=str(tmp_path))
    rel_path = await provider.save_image(
        garage_id=garage1.id,
        session_id=session1.id,
        image_type="entry",
        data=b"REAL_TEST_IMAGE_BYTES_555",
    )
    session1.plate_image_path = rel_path
    await db_session.flush()

    app = FastAPI()
    app.include_router(garage_management_api_router)
    app.dependency_overrides[get_db] = lambda: db_session

    transport = ASGITransport(app=app)

    # 1. Caller from Garage 2 attempting to view Garage 1's plate image -> 404 TENANT_NOT_FOUND
    ctx_garage2 = TenantContext(
        user_id=op2.id,
        role=UserRole.OPERATOR,
        garage_id=garage2.id,
        owner_id=garage2.owner_id,
        is_super_admin=False,
    )
    app.dependency_overrides[get_tenant_context] = lambda: ctx_garage2

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res_cross = await client.get(f"/api/v1/garages/{garage1.id}/sessions/{session1.id}/plate-image")
        assert res_cross.status_code == 404
        assert res_cross.headers.get("X-Error-Code") == "TENANT_NOT_FOUND"

    # 2. Caller from Garage 1 -> 200 OK
    ctx_garage1 = TenantContext(
        user_id=op1.id,
        role=UserRole.OPERATOR,
        garage_id=garage1.id,
        owner_id=garage1.owner_id,
        is_super_admin=False,
    )
    app.dependency_overrides[get_tenant_context] = lambda: ctx_garage1

    # Temporarily patch provider base_dir to match tmp_path
    from routes import garage_management_api
    original_provider = garage_management_api.LocalStorageProvider
    garage_management_api.LocalStorageProvider = lambda *args, **kwargs: LocalStorageProvider(base_dir=str(tmp_path))

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res_auth = await client.get(f"/api/v1/garages/{garage1.id}/sessions/{session1.id}/plate-image")
            assert res_auth.status_code == 200
            assert res_auth.content == b"REAL_TEST_IMAGE_BYTES_555"
            assert "image/webp" in res_auth.headers.get("content-type", "")
    finally:
        garage_management_api.LocalStorageProvider = original_provider


def test_main_does_not_contain_open_media_mount():
    """Verifies main.py enforces security by strictly forbidding app.mount('/media', ...)."""
    with open("main.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert 'app.mount("/media"' not in content
    assert "app.mount('/media'" not in content


# ===========================================================================
# Task 8.5: Media Retention Worker Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_media_retention_periodic_cleanup(tmp_path):
    """Verifies cleanup_expired_media removes files older than 90 days and preserves recent files."""
    media_dir = tmp_path / "media" / "garages" / "1" / "plates"
    media_dir.mkdir(parents=True)

    old_file = media_dir / "100_entry.webp"
    recent_file = media_dir / "200_entry.webp"

    old_file.write_bytes(b"OLD_IMAGE_DATA")
    recent_file.write_bytes(b"RECENT_IMAGE_DATA")

    # Set old_file mtime to 95 days ago
    past_time = time.time() - (95 * 86400)
    os.utime(str(old_file), (past_time, past_time))

    # Set recent_file mtime to 10 days ago
    recent_time = time.time() - (10 * 86400)
    os.utime(str(recent_file), (recent_time, recent_time))

    # Execute cleanup
    stats = await cleanup_expired_media(base_media_dir=str(tmp_path / "media"), retention_days=90)

    assert stats["scanned_count"] == 2
    assert stats["deleted_count"] == 1

    # Verify old file is gone, recent file remains
    assert not old_file.exists()
    assert recent_file.exists()
    assert recent_file.read_bytes() == b"RECENT_IMAGE_DATA"
