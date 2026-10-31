from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import require_operator, require_any_role
from models.user import User, UserRole
from models.shift import Shift
from repositories.session_repo import ParkingSessionRepository
from repositories.shift_repo import ShiftRepository
from schemas.common import PaginatedResponse
from schemas.shift import (
    ShiftOpenRequest,
    ShiftCloseRequest,
    ShiftResponse,
    ShiftSummaryResponse,
)
from schemas.parking_session import SessionResponse
from services.shift_service import ShiftService
from services.audit_service import AuditService
from services.exceptions import (
    ShiftAlreadyOpenError,
    ShiftNotFoundError,
    ShiftNotOwnedError,
)

router = APIRouter(prefix="/api/v1/shifts", tags=["shifts"])

def get_shift_service(db: AsyncSession = Depends(get_db)) -> ShiftService:
    audit_service = AuditService(db)
    return ShiftService(db, audit_service)

def get_shift_repo(db: AsyncSession = Depends(get_db)) -> ShiftRepository:
    return ShiftRepository(db)

def get_session_repo(db: AsyncSession = Depends(get_db)) -> ParkingSessionRepository:
    return ParkingSessionRepository(db)

@router.post("/", status_code=201)
async def open_shift(
    data: ShiftOpenRequest,
    current_user: User = Depends(require_operator),
    shift_service: ShiftService = Depends(get_shift_service),
):
    try:
        shift = await shift_service.open_shift(
            operator_id=current_user.id,
            gate_number=current_user.gate_number,
            opening_cash_egp=data.opening_cash_egp,
        )
    except ShiftAlreadyOpenError as e:
        raise HTTPException(
            status_code=409,
            detail=e.message,
            headers={"X-Error-Code": "SHIFT_ALREADY_OPEN"},
        )
    return {"data": ShiftResponse.model_validate(shift).model_dump(mode="json")}

@router.get("/active")
async def get_active_shift(
    operator_id: int | None = None,
    current_user: User = Depends(require_any_role),
    shift_service: ShiftService = Depends(get_shift_service),
):
    if current_user.role == UserRole.ADMIN:
        if operator_id is None:
            raise HTTPException(
                status_code=400,
                detail="operator_id query parameter is required for admin",
                headers={"X-Error-Code": "OPERATOR_ID_REQUIRED"},
            )
        target_id = operator_id
    else:
        target_id = current_user.id

    shift = await shift_service.get_active_shift(target_id)
    if not shift:
        raise HTTPException(
            status_code=404,
            detail="No active shift found",
            headers={"X-Error-Code": "SHIFT_NOT_FOUND"},
        )
    return {"data": ShiftResponse.model_validate(shift).model_dump(mode="json")}

@router.patch("/{shift_id}/close")
async def close_shift(
    shift_id: int,
    data: ShiftCloseRequest,
    current_user: User = Depends(require_operator),
    shift_service: ShiftService = Depends(get_shift_service),
):
    try:
        summary = await shift_service.close_shift(
            shift_id=shift_id,
            operator_id=current_user.id,
            closing_cash_piastres=data.closing_cash_egp,
        )
    except ShiftNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail=e.message,
            headers={"X-Error-Code": "SHIFT_NOT_FOUND"},
        )
    except ShiftNotOwnedError as e:
        raise HTTPException(
            status_code=403,
            detail=e.message,
            headers={"X-Error-Code": "INSUFFICIENT_PERMISSIONS"},
        )
    return {"data": ShiftSummaryResponse.model_validate(summary).model_dump(mode="json")}

@router.get("/{shift_id}/summary")
async def get_shift_summary(
    shift_id: int,
    current_user: User = Depends(require_any_role),
    shift_repo: ShiftRepository = Depends(get_shift_repo),
    shift_service: ShiftService = Depends(get_shift_service),
):
    gid = current_user.garage_id if current_user.role != UserRole.SUPER_ADMIN else None
    shift = await shift_repo.get_by_id(shift_id, garage_id=gid)
    if not shift:
        raise HTTPException(
            status_code=404,
            detail="Shift not found",
            headers={"X-Error-Code": "SHIFT_NOT_FOUND"},
        )
    summary = await shift_service._compute_summary(shift, shift.closing_cash_egp)
    return {"data": ShiftSummaryResponse.model_validate(summary).model_dump(mode="json")}

@router.get("/{shift_id}/sessions", response_model=PaginatedResponse[SessionResponse])
async def get_shift_sessions(
    shift_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    current_user: User = Depends(require_any_role),
    session_repo: ParkingSessionRepository = Depends(get_session_repo),
):
    sessions, total = await session_repo.get_by_shift(shift_id=shift_id, page=page, size=size)
    responses = [SessionResponse.model_validate(s) for s in sessions]
    return PaginatedResponse[SessionResponse](
        data=responses,
        total=total,
        page=page,
        size=size,
    )
