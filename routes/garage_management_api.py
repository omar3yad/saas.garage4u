import hashlib
import secrets
from typing import Any
from fastapi import APIRouter, Depends, Query, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import (
    get_tenant_context,
    get_current_user,
    require_owner_or_admin,
    require_manager_or_above,
    require_operator_or_above,
)
from utils.tenant_context import TenantContext
from models.user import User, UserRole
from repositories.garage_repo import GarageRepository
from repositories.garage_config_repo import GarageConfigRepository
from repositories.garage_gate_repo import GarageGateRepository
from repositories.edge_device_repo import EdgeDeviceRepository
from repositories.session_repo import ParkingSessionRepository
from repositories.shift_repo import ShiftRepository
from services.config_service import ConfigService
from services.audit_service import AuditService
from services.storage import LocalStorageProvider
from services.exceptions import (
    TenantNotFoundError,
    FeatureNotEntitledError,
    ExitPhotoRequiresANPRError,
    ConfigLockedActiveSessionsError,
    ConfigVersionConflictError,
)
from schemas.garage_config import GarageConfigUpdate, GarageConfigResponse
from schemas.garage_gate import GarageGateCreate, GarageGateResponse
from schemas.edge_device import (
    EdgeDeviceCreate,
    EdgeDeviceResponse,
    EdgeDeviceTokenResponse,
    EdgeDeviceGateAssign,
    EdgeDeviceGateResponse,
)
from schemas.parking_session import SessionResponse

router = APIRouter(prefix="/api/v1/garages", tags=["garage-management"])


class PlateOverrideRequest(BaseModel):
    plate_number: str = Field(min_length=1, max_length=30)
    override_reason: str | None = None


# ===========================================================================
# 1. Garage Configuration Routes
# ===========================================================================

@router.get("/{garage_id}/config", response_model=GarageConfigResponse)
async def get_garage_config(
    garage_id: int,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    current_user: User = Depends(require_manager_or_above),
    db: AsyncSession = Depends(get_db),
):
    """Retrieves operational configuration for the specified garage."""
    config_repo = GarageConfigRepository(db)
    config = await config_repo.get_by_garage_id(garage_id)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found for this garage",
            headers={"X-Error-Code": "CONFIG_NOT_FOUND"},
        )
    return config


@router.put("/{garage_id}/config", response_model=GarageConfigResponse)
async def update_garage_config(
    garage_id: int,
    data: GarageConfigUpdate,
    expected_version: int = Query(..., ge=1),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    current_user: User = Depends(require_owner_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Updates operational configuration with entitlement enforcement and optimistic locking."""
    config_service = ConfigService(db)
    try:
        updated = await config_service.update_config(
            garage_id=garage_id,
            update_data=data,
            expected_version=expected_version,
        )
        await db.commit()
        await db.refresh(updated)
        return updated
    except TenantNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e), headers={"X-Error-Code": "TENANT_NOT_FOUND"})
    except FeatureNotEntitledError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e), headers={"X-Error-Code": "FEATURE_NOT_ENTITLED"})
    except ExitPhotoRequiresANPRError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e), headers={"X-Error-Code": "EXIT_PHOTO_REQUIRES_ANPR"})
    except ConfigLockedActiveSessionsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e), headers={"X-Error-Code": "CONFIG_LOCKED_ACTIVE_SESSIONS"})
    except ConfigVersionConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e), headers={"X-Error-Code": "CONFIG_VERSION_CONFLICT"})


# ===========================================================================
# 2. Garage Gates Routes
# ===========================================================================

@router.get("/{garage_id}/gates", response_model=list[GarageGateResponse])
async def list_garage_gates(
    garage_id: int,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    current_user: User = Depends(require_manager_or_above),
    db: AsyncSession = Depends(get_db),
):
    """Lists all configured gates for this garage."""
    gate_repo = GarageGateRepository(db)
    gates = await gate_repo.get_all_by_garage(garage_id)
    return [GarageGateResponse.model_validate(g) for g in gates]


@router.post("/{garage_id}/gates", response_model=GarageGateResponse, status_code=status.HTTP_201_CREATED)
async def create_garage_gate(
    garage_id: int,
    data: GarageGateCreate,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    current_user: User = Depends(require_owner_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Adds a new gate to the garage, strictly enforcing max_gates quota."""
    garage_repo = GarageRepository(db)
    gate_repo = GarageGateRepository(db)

    garage = await garage_repo.get_by_id(garage_id)
    if not garage:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Garage not found",
            headers={"X-Error-Code": "TENANT_NOT_FOUND"},
        )

    current_gates_count = await gate_repo.count_by_garage(garage_id)
    if current_gates_count >= garage.max_gates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot exceed maximum gate limit of {garage.max_gates} for this garage",
            headers={"X-Error-Code": "MAX_GATES_EXCEEDED"},
        )

    existing_gate = await gate_repo.get_by_number(garage_id, data.gate_number)
    if existing_gate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Gate number {data.gate_number} already exists in this garage",
            headers={"X-Error-Code": "GATE_NUMBER_EXISTS"},
        )

    gate = await gate_repo.create(garage_id=garage_id, **data.model_dump())
    await db.commit()
    await db.refresh(gate)
    return gate


# ===========================================================================
# 3. Edge Devices & Token Rotation Routes
# ===========================================================================

@router.get("/{garage_id}/edge-devices", response_model=list[EdgeDeviceResponse])
async def list_edge_devices(
    garage_id: int,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    current_user: User = Depends(require_owner_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Lists all camera edge PC devices scoped to this garage."""
    device_repo = EdgeDeviceRepository(db)
    devices = await device_repo.get_by_garage(garage_id)
    return [EdgeDeviceResponse.model_validate(d) for d in devices]


@router.post("/{garage_id}/edge-devices", response_model=EdgeDeviceTokenResponse, status_code=status.HTTP_201_CREATED)
async def create_edge_device(
    garage_id: int,
    data: EdgeDeviceCreate,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    current_user: User = Depends(require_owner_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Registers a new edge camera device, generating a one-time raw secret token."""
    device_repo = EdgeDeviceRepository(db)
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    device = await device_repo.create(
        garage_id=garage_id,
        device_name=data.device_name,
        token_hash=token_hash,
    )
    await db.commit()
    await db.refresh(device)

    return EdgeDeviceTokenResponse(
        device=EdgeDeviceResponse.model_validate(device),
        raw_token=raw_token,
    )


@router.post("/{garage_id}/edge-devices/{device_id}/rotate-token", response_model=EdgeDeviceTokenResponse)
async def rotate_edge_device_token(
    garage_id: int,
    device_id: int,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    current_user: User = Depends(require_owner_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Rotates an edge device token: generates a new random token, updates hash,
    and returns the raw token strictly once.
    """
    device_repo = EdgeDeviceRepository(db)
    device = await device_repo.get_by_id(device_id, garage_id=garage_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Edge device not found in this garage",
            headers={"X-Error-Code": "DEVICE_NOT_FOUND"},
        )

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    device.token_hash = token_hash
    device.revoked_at = None
    await db.flush()
    await db.commit()
    await db.refresh(device)

    return EdgeDeviceTokenResponse(
        device=EdgeDeviceResponse.model_validate(device),
        raw_token=raw_token,
    )


# ===========================================================================
# 4. Session Details, Plate Override, and Plate Image Routes
# ===========================================================================

@router.get("/{garage_id}/sessions/{session_id}", response_model=SessionResponse)
async def get_garage_session(
    garage_id: int,
    session_id: int,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    current_user: User = Depends(require_operator_or_above),
    db: AsyncSession = Depends(get_db),
):
    """Fetches details for a specific parking session within the garage scope."""
    session_repo = ParkingSessionRepository(db)
    session = await session_repo.get_by_id(session_id, garage_id=garage_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found in this garage",
            headers={"X-Error-Code": "SESSION_NOT_FOUND"},
        )
    return session


@router.post("/{garage_id}/sessions/{session_id}/plate-override", response_model=SessionResponse)
async def override_session_plate(
    garage_id: int,
    session_id: int,
    data: PlateOverrideRequest,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    current_user: User = Depends(require_operator_or_above),
    db: AsyncSession = Depends(get_db),
):
    """Manually overrides/corrects the vehicle license plate on an active session.
    RBAC Matrix Enforcement:
    - OWNER / MANAGER / SUPER_ADMIN: Allowed unconditionally within tenant.
    - OPERATOR: Allowed strictly if they have an active open shift in this garage.
    """
    if current_user.role == UserRole.OPERATOR:
        shift_repo = ShiftRepository(db)
        active_shift = await shift_repo.get_active_for_operator(current_user.id)
        if not active_shift or active_shift.garage_id != garage_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operator must have an active shift to override vehicle plate",
                headers={"X-Error-Code": "OPERATOR_SHIFT_REQUIRED"},
            )

    session_repo = ParkingSessionRepository(db)
    session = await session_repo.get_by_id(session_id, garage_id=garage_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found in this garage",
            headers={"X-Error-Code": "SESSION_NOT_FOUND"},
        )

    old_plate = session.plate_number
    session.plate_number = data.plate_number.strip().upper()
    await db.flush()

    audit_service = AuditService(db)
    await audit_service.log(
        actor_id=current_user.id,
        action="PLATE_OVERRIDE",
        entity_type="parking_session",
        entity_id=session.id,
        before={"plate_number": old_plate},
        after={"plate_number": session.plate_number, "reason": data.override_reason},
        garage_id=garage_id,
    )

    await db.commit()
    await db.refresh(session)
    return session


@router.get("/{garage_id}/sessions/{session_id}/plate-image")
async def get_session_plate_image(
    garage_id: int,
    session_id: int,
    image_type: str = Query("entry", pattern=r"^(entry|exit)$"),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    """Authenticated endpoint to serve session plate images.
    Enforces tenant scoping, preventing cross-tenant access with 404 TENANT_NOT_FOUND.
    Disallows unauthenticated public access.
    """
    # 1. Enforce tenant boundary
    if not tenant_ctx.is_super_admin:
        if tenant_ctx.garage_id is not None and tenant_ctx.garage_id != garage_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Garage not found or inaccessible",
                headers={"X-Error-Code": "TENANT_NOT_FOUND"},
            )

    session_repo = ParkingSessionRepository(db)
    session = await session_repo.get_by_id(session_id, garage_id=garage_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found in this garage",
            headers={"X-Error-Code": "SESSION_NOT_FOUND"},
        )

    rel_path = session.plate_image_path if image_type == "entry" else session.exit_image_path
    if not rel_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {image_type} image recorded for this session",
            headers={"X-Error-Code": "IMAGE_NOT_FOUND"},
        )

    storage = LocalStorageProvider()
    abs_path = await storage.get_image_path(rel_path)
    if not abs_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image file not found on disk",
            headers={"X-Error-Code": "IMAGE_NOT_FOUND"},
        )

    return FileResponse(abs_path, media_type="image/webp")


__all__ = ["router"]
