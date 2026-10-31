from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import require_admin, require_any_role
from models.user import User
from models.parking_card import CardStatus
from repositories.card_repo import ParkingCardRepository
from schemas.common import PaginatedResponse
from schemas.parking_card import (
    ParkingCardCreate,
    ParkingCardBulkCreate,
    ParkingCardRangeCreate,
    ParkingCardResponse,
    ParkingCardStatusUpdate,
)
from services.card_service import CardService
from services.audit_service import AuditService
from services.exceptions import InvalidBarcodeFormatError

router = APIRouter(prefix="/api/v1/cards", tags=["cards"])

def get_card_service(db: AsyncSession = Depends(get_db)) -> CardService:
    return CardService(db)

def get_card_repo(db: AsyncSession = Depends(get_db)) -> ParkingCardRepository:
    return ParkingCardRepository(db)

@router.post("/", status_code=201)
async def create_card(
    data: ParkingCardCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    card_service: CardService = Depends(get_card_service),
    card_repo: ParkingCardRepository = Depends(get_card_repo),
):
    try:
        normalized = card_service.normalize_code(data.card_code)
    except InvalidBarcodeFormatError as e:
        raise HTTPException(
            status_code=422,
            detail=e.message,
            headers={"X-Error-Code": "INVALID_BARCODE_FORMAT"},
        )

    garage_id = current_user.garage_id
    if garage_id is None:
        raise HTTPException(
            status_code=400,
            detail="User has no assigned garage",
            headers={"X-Error-Code": "GARAGE_REQUIRED"},
        )

    existing = await card_repo.get_by_code(normalized, garage_id=garage_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail="كود الكارت مسجل مسبقاً",
            headers={"X-Error-Code": "CARD_CODE_ALREADY_EXISTS"},
        )

    card = await card_repo.create(normalized, garage_id=garage_id)
    await db.commit()
    await db.refresh(card)

    return {"data": ParkingCardResponse.model_validate(card).model_dump(mode="json")}

@router.post("/bulk", status_code=201)
async def create_cards_bulk(
    data: ParkingCardBulkCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    card_service: CardService = Depends(get_card_service),
    card_repo: ParkingCardRepository = Depends(get_card_repo),
):
    normalized_codes = []
    seen = set()
    for code in data.card_codes:
        try:
            norm = card_service.normalize_code(code)
            if norm not in seen:
                normalized_codes.append(norm)
                seen.add(norm)
        except InvalidBarcodeFormatError as e:
            raise HTTPException(
                status_code=422,
                detail=f"كود غير صالح '{code}': {e.message}",
                headers={"X-Error-Code": "INVALID_BARCODE_FORMAT"},
            )

    garage_id = current_user.garage_id
    if garage_id is None:
        raise HTTPException(
            status_code=400,
            detail="User has no assigned garage",
            headers={"X-Error-Code": "GARAGE_REQUIRED"},
        )

    conflicting = await card_repo.get_existing_codes(normalized_codes)
    new_codes = [c for c in normalized_codes if c not in conflicting]
    
    if not new_codes:
        raise HTTPException(
            status_code=409,
            detail=f"جميع الكروت المدخلة مسجلة مسبقاً ({len(conflicting)} كارت)",
            headers={"X-Error-Code": "ALL_CARDS_EXIST"},
        )

    cards = await card_repo.bulk_create(new_codes, garage_id=garage_id)
    await db.commit()

    return {
        "created": len(cards),
        "skipped": len(conflicting),
        "conflicting": conflicting[:20],
        "total_requested": len(normalized_codes),
    }

@router.post("/range", status_code=201)
async def create_cards_range(
    data: ParkingCardRangeCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    card_service: CardService = Depends(get_card_service),
    card_repo: ParkingCardRepository = Depends(get_card_repo),
):
    garage_id = current_user.garage_id
    if garage_id is None:
        raise HTTPException(
            status_code=400,
            detail="User has no assigned garage",
            headers={"X-Error-Code": "GARAGE_REQUIRED"},
        )

    if data.start_number > data.end_number:
        raise HTTPException(
            status_code=422,
            detail="رقم البداية يجب أن يكون أصغر من أو يساوي رقم النهاية",
            headers={"X-Error-Code": "INVALID_RANGE"},
        )
    
    count = data.end_number - data.start_number + 1
    if count > 5000:
        raise HTTPException(
            status_code=422,
            detail=f"الحد الأقصى للإضافة في المرة الواحدة هو 5000 كارت (المطلوب {count})",
            headers={"X-Error-Code": "RANGE_TOO_LARGE"},
        )

    codes = []
    for num in range(data.start_number, data.end_number + 1):
        num_str = str(num).zfill(data.pad_zeros) if data.pad_zeros > 0 else str(num)
        raw_code = f"{data.prefix}{num_str}{data.suffix}"
        try:
            codes.append(card_service.normalize_code(raw_code))
        except InvalidBarcodeFormatError as e:
            raise HTTPException(
                status_code=422,
                detail=f"كود ناتج غير صالح '{raw_code}': {e.message}",
                headers={"X-Error-Code": "INVALID_BARCODE_FORMAT"},
            )

    conflicting = await card_repo.get_existing_codes(codes)
    conflicting_set = set(conflicting)
    new_codes = [c for c in codes if c not in conflicting_set]

    if not new_codes:
        raise HTTPException(
            status_code=409,
            detail=f"جميع الكروت في هذا النطاق مسجلة مسبقاً ({len(conflicting)} كارت)",
            headers={"X-Error-Code": "ALL_CARDS_EXIST"},
        )

    cards = await card_repo.bulk_create(new_codes, garage_id=garage_id)
    await db.commit()

    return {
        "created": len(cards),
        "skipped": len(conflicting),
        "total_in_range": count,
        "first_card": codes[0],
        "last_card": codes[-1],
    }

@router.get("/", response_model=PaginatedResponse[ParkingCardResponse])
async def get_cards(
    status: CardStatus | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_admin),
    card_repo: ParkingCardRepository = Depends(get_card_repo),
):
    gid = current_user.garage_id if current_user.role != UserRole.SUPER_ADMIN else None
    cards, total = await card_repo.get_all(garage_id=gid, status=status, search=search, page=page, size=size)
    responses = [ParkingCardResponse.model_validate(c) for c in cards]
    return PaginatedResponse[ParkingCardResponse](
        data=responses,
        total=total,
        page=page,
        size=size,
    )


@router.get("/{card_code}")
async def get_card_by_code(
    card_code: str,
    current_user: User = Depends(require_any_role),
    card_service: CardService = Depends(get_card_service),
    card_repo: ParkingCardRepository = Depends(get_card_repo),
):
    try:
        normalized = card_service.normalize_code(card_code)
    except InvalidBarcodeFormatError as e:
        raise HTTPException(
            status_code=422,
            detail=e.message,
            headers={"X-Error-Code": "INVALID_BARCODE_FORMAT"},
        )

    gid = current_user.garage_id if current_user.role != UserRole.SUPER_ADMIN else None
    card = await card_repo.get_by_code(normalized, garage_id=gid)
    if not card:
        raise HTTPException(
            status_code=404,
            detail=f"Card '{card_code}' not found",
            headers={"X-Error-Code": "CARD_NOT_FOUND"},
        )
    return {"data": ParkingCardResponse.model_validate(card).model_dump(mode="json")}

@router.patch("/{card_code}/status")
async def update_card_status(
    card_code: str,
    data: ParkingCardStatusUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    card_service: CardService = Depends(get_card_service),
    card_repo: ParkingCardRepository = Depends(get_card_repo),
):
    try:
        normalized = card_service.normalize_code(card_code)
    except InvalidBarcodeFormatError as e:
        raise HTTPException(
            status_code=422,
            detail=e.message,
            headers={"X-Error-Code": "INVALID_BARCODE_FORMAT"},
        )

    gid = current_user.garage_id if current_user.role != UserRole.SUPER_ADMIN else None
    card = await card_repo.get_by_code(normalized, garage_id=gid)
    if not card:
        raise HTTPException(
            status_code=404,
            detail=f"Card '{card_code}' not found",
            headers={"X-Error-Code": "CARD_NOT_FOUND"},
        )

    old_status = card.status
    card = await card_service.set_status(card, data.status)
    await db.commit()
    await db.refresh(card)

    audit_service = AuditService(db)
    await audit_service.log(
        actor_id=current_user.id,
        action="CARD_STATUS_CHANGED",
        entity_type="parking_card",
        entity_id=card.id,
        before={"status": old_status.value},
        after={"status": card.status.value},
    )

    return {"data": ParkingCardResponse.model_validate(card).model_dump(mode="json")}
