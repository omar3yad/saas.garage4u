from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import require_super_admin
from models.user import User
from repositories.owner_repo import OwnerRepository
from repositories.garage_repo import GarageRepository
from repositories.garage_config_repo import GarageConfigRepository
from repositories.garage_gate_repo import GarageGateRepository
from repositories.audit_log_repo import AuditLogRepository
from schemas.owner import OwnerCreate, OwnerResponse
from schemas.garage import GarageCreate, GarageResponse
from schemas.audit_log import AuditLogResponse
from schemas.common import PaginatedResponse

router = APIRouter(prefix="/api/v1/platform", tags=["platform-admin"])


@router.post("/owners", response_model=OwnerResponse, status_code=status.HTTP_201_CREATED)
async def create_owner(
    data: OwnerCreate,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Creates a new owner account on the platform with quota parameters."""
    owner_repo = OwnerRepository(db)
    existing = await owner_repo.get_by_email(data.contact_email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Owner with this email already exists",
            headers={"X-Error-Code": "OWNER_EMAIL_EXISTS"},
        )

    owner = await owner_repo.create(**data.model_dump())
    await db.commit()
    await db.refresh(owner)
    return owner


@router.get("/owners", response_model=PaginatedResponse[OwnerResponse])
async def list_owners(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Lists all owners on the platform with pagination."""
    owner_repo = OwnerRepository(db)
    owners, total = await owner_repo.get_all(page=page, size=size)
    return PaginatedResponse[OwnerResponse](
        data=[OwnerResponse.model_validate(o) for o in owners],
        total=total,
        page=page,
        size=size,
    )


@router.post("/garages", response_model=GarageResponse, status_code=status.HTTP_201_CREATED)
async def create_garage(
    data: GarageCreate,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Creates a new garage and assigns initial entitlements.
    Strictly verifies that the owner's total garages do not exceed max_garages_allowed (returns 422 MAX_GARAGES_EXCEEDED).
    """
    owner_repo = OwnerRepository(db)
    garage_repo = GarageRepository(db)
    config_repo = GarageConfigRepository(db)
    gate_repo = GarageGateRepository(db)

    # 1. Verify owner existence
    owner = await owner_repo.get_by_id(data.owner_id)
    if not owner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Owner not found",
            headers={"X-Error-Code": "OWNER_NOT_FOUND"},
        )

    # 2. Strict Quota Enforcement: Check max_garages_allowed
    current_garages_count = await owner_repo.count_garages(data.owner_id)
    if current_garages_count >= owner.max_garages_allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Owner has reached the maximum allowed garages limit ({owner.max_garages_allowed})",
            headers={"X-Error-Code": "MAX_GARAGES_EXCEEDED"},
        )

    # 3. Check code uniqueness
    existing_code = await garage_repo.get_by_code(data.code)
    if existing_code:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Garage with code '{data.code}' already exists",
            headers={"X-Error-Code": "GARAGE_CODE_EXISTS"},
        )

    # 4. Create garage
    garage = await garage_repo.create(**data.model_dump())

    # 5. Seed default configuration
    await config_repo.create(
        garage_id=garage.id,
        anpr_enabled=garage.can_use_anpr,
        subscriptions_enabled=garage.can_use_subscriptions,
    )

    # 6. Seed default Gate 1
    await gate_repo.create(
        garage_id=garage.id,
        gate_number=1,
        name="Gate 1",
        gate_type="MANUAL_BIDIRECTIONAL",
        paper_width_mm=80,
    )

    await db.commit()
    await db.refresh(garage)
    return garage


@router.get("/audit-trail", response_model=PaginatedResponse[AuditLogResponse])
async def get_platform_audit_trail(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    action: str | None = Query(None),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Retrieves system-wide audit trail logs across all tenants."""
    audit_repo = AuditLogRepository(db)
    logs, total = await audit_repo.get_all(action=action, page=page, size=size)
    return PaginatedResponse[AuditLogResponse](
        data=[AuditLogResponse.model_validate(log) for log in logs],
        total=total,
        page=page,
        size=size,
    )


__all__ = ["router"]
