import hashlib
import json
import logging
from datetime import datetime
from fastapi import APIRouter, Header, UploadFile, File, Form, Depends, HTTPException, Request, status
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.edge_device import EdgeDeviceGate
from repositories.edge_device_repo import EdgeDeviceRepository
from repositories.session_repo import ParkingSessionRepository
from services.storage import LocalStorageProvider, FileTooLargeError
from services.edge_hub import EdgeHub

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/edge", tags=["edge"])


@router.post("/upload-capture", status_code=status.HTTP_201_CREATED)
async def upload_capture(
    request: Request,
    file: UploadFile = File(...),
    session_id: int = Form(...),
    gate_number: int = Form(...),
    capture_type: str = Form("entry"),
    plate_number: str | None = Form(None),
    confidence: float | None = Form(None),
    x_edge_token: str | None = Header(None, alias="X-Edge-Token"),
    db: AsyncSession = Depends(get_db),
):
    """Authenticated edge image upload endpoint.
    Verifies device token, immediately sets garage RLS context,
    validates session & gate assignments, enforces 1.5MB strict limit,
    and publishes a lightweight (<8KB) event notification.
    """
    if not x_edge_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Edge-Token header",
            headers={"X-Error-Code": "EDGE_AUTH_FAILED"},
        )

    # 1. Global authentication via SHA-256 token hash (edge_devices is RLS-exempt)
    token_hash = hashlib.sha256(x_edge_token.strip().encode()).hexdigest()
    edge_repo = EdgeDeviceRepository(db)
    device = await edge_repo.get_by_token_hash(token_hash)
    if not device or device.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked edge device token",
            headers={"X-Error-Code": "EDGE_AUTH_FAILED"},
        )

    # 2. Immediately set transaction RLS context for device's garage
    await db.execute(
        text("SELECT set_config('app.current_garage_id', :gid, true);"),
        {"gid": str(device.garage_id)},
    )

    # 3. Verify session belongs to this device's garage (enforced under RLS)
    session_repo = ParkingSessionRepository(db)
    session = await session_repo.get_by_id(session_id, garage_id=device.garage_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or belongs to another garage",
            headers={"X-Error-Code": "TENANT_NOT_FOUND"},
        )

    # 4. Verify gate is assigned to this edge device in edge_device_gates
    gate_query = select(EdgeDeviceGate).where(
        EdgeDeviceGate.device_id == device.id,
        EdgeDeviceGate.garage_id == device.garage_id,
        EdgeDeviceGate.gate_number == gate_number,
        EdgeDeviceGate.is_active == True,
    ).limit(1)
    gate_res = await db.execute(gate_query)
    gate_assignment = gate_res.scalars().first()
    if not gate_assignment:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Gate {gate_number} is not assigned to this edge device",
            headers={"X-Error-Code": "GATE_NOT_ASSIGNED"},
        )

    # 5. Read and enforce strict file size (1,500,000 bytes)
    content = await file.read()
    if len(content) > 1_500_000:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Image size ({len(content)} bytes) exceeds 1.5MB limit",
            headers={"X-Error-Code": "IMAGE_SIZE_EXCEEDED"},
        )

    # 6. Save isolated image via LocalStorageProvider
    storage = LocalStorageProvider(max_bytes=1_500_000)
    try:
        rel_path = await storage.save_image(
            garage_id=device.garage_id,
            session_id=session.id,
            image_type=capture_type,
            data=content,
        )
    except FileTooLargeError as e:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(e),
            headers={"X-Error-Code": "IMAGE_SIZE_EXCEEDED"},
        )

    # 7. Update session columns
    clean_type = capture_type.strip().lower()
    if clean_type == "entry":
        session.plate_image_path = rel_path
        session.plate_capture_status = "SUCCESS"
        session.plate_captured_at = datetime.utcnow()
        if confidence is not None:
            session.plate_confidence = confidence
        if plate_number:
            session.plate_number = plate_number
    else:
        session.exit_image_path = rel_path

    await db.commit()
    await db.refresh(session)

    # 8. Publish lightweight text notification via EventBus (under 8KB, strictly NO base64)
    hub: EdgeHub | None = getattr(request.app.state, "edge_hub", None)
    if hub and getattr(hub, "bus", None):
        try:
            await hub.bus.publish(
                "pgms_edge_result",
                json.dumps({
                    "event": "IMAGE_UPLOADED",
                    "session_id": session.id,
                    "garage_id": device.garage_id,
                    "gate_number": gate_number,
                    "image_type": clean_type,
                    "path": rel_path,
                }),
            )
        except Exception as e:
            logger.warning("Failed to publish image upload event: %s", str(e))

    return {
        "status": "success",
        "session_id": session.id,
        "image_path": rel_path,
    }


__all__ = ["router"]
