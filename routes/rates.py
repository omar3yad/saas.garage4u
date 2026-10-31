from datetime import datetime
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import require_admin, require_any_role
from models.user import User
from repositories.rate_repo import PricingRuleRepository
from schemas.common import PaginatedResponse
from schemas.pricing_rule import PricingRuleCreate, PricingRuleUpdate, PricingRuleResponse
from schemas.parking_session import PriceBreakdownResponse
from services.pricing_service import PricingService
from services.audit_service import AuditService
from services.exceptions import NoPricingRuleError, RateLabelAlreadyExistsError

router = APIRouter(prefix="/api/v1/rates", tags=["rates"])

def get_pricing_service(db: AsyncSession = Depends(get_db)) -> PricingService:
    return PricingService(db)

def get_pricing_repo(db: AsyncSession = Depends(get_db)) -> PricingRuleRepository:
    return PricingRuleRepository(db)

@router.get("/active")
async def get_active_rate(
    current_user: User = Depends(require_any_role),
    pricing_service: PricingService = Depends(get_pricing_service),
):
    gid = current_user.garage_id if current_user.role != UserRole.SUPER_ADMIN else None
    try:
        rule = await pricing_service.get_active_rule(garage_id=gid)
    except NoPricingRuleError as e:
        raise HTTPException(
            status_code=503,
            detail=e.message,
            headers={"X-Error-Code": "NO_ACTIVE_PRICING_RULE"},
        )
    return {"data": PricingRuleResponse.model_validate(rule).model_dump(mode="json")}

@router.get("/preview")
async def preview_rate(
    entry_time: datetime,
    current_user: User = Depends(require_any_role),
    pricing_service: PricingService = Depends(get_pricing_service),
):
    gid = current_user.garage_id if current_user.role != UserRole.SUPER_ADMIN else None
    try:
        calc = await pricing_service.preview(entry_time, garage_id=gid)
    except NoPricingRuleError as e:
        raise HTTPException(
            status_code=503,
            detail=e.message,
            headers={"X-Error-Code": "NO_ACTIVE_PRICING_RULE"},
        )
    return {"data": PriceBreakdownResponse.model_validate(calc).model_dump(mode="json")}

@router.get("/", response_model=PaginatedResponse[PricingRuleResponse])
async def get_rates(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_admin),
    pricing_repo: PricingRuleRepository = Depends(get_pricing_repo),
):
    gid = current_user.garage_id if current_user.role != UserRole.SUPER_ADMIN else None
    rules, total = await pricing_repo.get_all(garage_id=gid, page=page, size=size)
    responses = [PricingRuleResponse.model_validate(r) for r in rules]
    return PaginatedResponse[PricingRuleResponse](
        data=responses,
        total=total,
        page=page,
        size=size,
    )

@router.post("/", status_code=201)
async def create_rate(
    data: PricingRuleCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    pricing_repo: PricingRuleRepository = Depends(get_pricing_repo),
    pricing_service: PricingService = Depends(get_pricing_service),
):
    audit_service = AuditService(db)
    try:
        rule = await pricing_service.create_rule(
            data=data,
            admin_id=current_user.id,
            rate_repo=pricing_repo,
            audit_service=audit_service,
        )
    except RateLabelAlreadyExistsError as e:
        raise HTTPException(
            status_code=409,
            detail=str(e),
            headers={"X-Error-Code": "RATE_LABEL_ALREADY_EXISTS"},
        )

    return {"data": PricingRuleResponse.model_validate(rule).model_dump(mode="json")}

@router.put("/{rule_id}")
@router.patch("/{rule_id}")
async def update_rate(
    rule_id: int,
    data: PricingRuleUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    pricing_repo: PricingRuleRepository = Depends(get_pricing_repo),
    pricing_service: PricingService = Depends(get_pricing_service),
):
    audit_service = AuditService(db)
    try:
        rule = await pricing_service.update_rule(
            rule_id=rule_id,
            data=data,
            admin_id=current_user.id,
            rate_repo=pricing_repo,
            audit_service=audit_service,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail=str(e),
            headers={"X-Error-Code": "PRICING_RULE_NOT_FOUND"},
        )
    except RateLabelAlreadyExistsError as e:
        raise HTTPException(
            status_code=409,
            detail=str(e),
            headers={"X-Error-Code": "RATE_LABEL_ALREADY_EXISTS"},
        )

    return {"data": PricingRuleResponse.model_validate(rule).model_dump(mode="json")}

@router.patch("/{rule_id}/activate")
async def activate_rate(
    rule_id: int,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    pricing_repo: PricingRuleRepository = Depends(get_pricing_repo),
):
    try:
        rule = await pricing_repo.set_active(rule_id)
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail=str(e),
            headers={"X-Error-Code": "PRICING_RULE_NOT_FOUND"},
        )

    await db.commit()
    await db.refresh(rule)

    audit_service = AuditService(db)
    await audit_service.log(
        actor_id=current_user.id,
        action="RATE_ACTIVATED",
        entity_type="pricing_rule",
        entity_id=rule.id,
        before=None,
        after={"id": rule.id, "label": rule.label},
    )
    await db.commit()

    return {"data": PricingRuleResponse.model_validate(rule).model_dump(mode="json")}
