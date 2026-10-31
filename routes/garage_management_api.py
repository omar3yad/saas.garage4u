from fastapi import APIRouter, Depends, Query, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_tenant_context
from utils.tenant_context import TenantContext
from repositories.session_repo import ParkingSessionRepository
from services.storage import LocalStorageProvider

router = APIRouter(prefix="/api/v1/garages", tags=["garage-management"])


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

    # 2. Fetch session within garage scope
    session_repo = ParkingSessionRepository(db)
    session = await session_repo.get_by_id(session_id, garage_id=garage_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found in this garage",
            headers={"X-Error-Code": "SESSION_NOT_FOUND"},
        )

    # 3. Resolve relative path
    rel_path = session.plate_image_path if image_type == "entry" else session.exit_image_path
    if not rel_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {image_type} image recorded for this session",
            headers={"X-Error-Code": "IMAGE_NOT_FOUND"},
        )

    # 4. Resolve absolute path on disk via LocalStorageProvider
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
