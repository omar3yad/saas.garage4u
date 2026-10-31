from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from dependencies import require_admin, validate_role_assignment
from models.user import User, UserRole
from repositories.user_repo import UserRepository
from schemas.common import PaginatedResponse
from schemas.user import UserCreate, UserResponse, UserUpdatePassword, UserUpdate
from services.audit_service import AuditService
from services.auth_service import AuthService
from services.user_service import UserService

router = APIRouter(prefix="/api/v1/users", tags=["users"])

def get_user_service(
    db: AsyncSession = Depends(get_db),
    settings = Depends(get_settings),
) -> UserService:
    user_repo = UserRepository(db)
    auth_service = AuthService(settings)
    audit_service = AuditService(db)
    return UserService(db, user_repo, auth_service, audit_service)

@router.post("/", status_code=201)
async def create_user(
    data: UserCreate,
    current_user: User = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    validate_role_assignment(
        creator=current_user,
        target_role=data.role,
        target_garage_id=data.garage_id,
        target_owner_id=data.owner_id,
    )
    new_user = await user_service.create_user(data)
    return {"data": UserResponse.model_validate(new_user).model_dump(mode="json")}

@router.get("/", response_model=PaginatedResponse[UserResponse])
async def get_users(
    role: UserRole | None = None,
    is_active: bool | None = None,
    gate_number: int | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    gid = current_user.garage_id if current_user.role == UserRole.MANAGER else None
    oid = current_user.owner_id if current_user.role == UserRole.OWNER else None
    users, total = await user_service.get_all_users(
        garage_id=gid,
        owner_id=oid,
        role=role,
        is_active=is_active,
        gate_number=gate_number,
        page=page,
        size=size,
    )
    user_responses = [UserResponse.model_validate(u) for u in users]
    return PaginatedResponse[UserResponse](
        data=user_responses,
        total=total,
        page=page,
        size=size,
    )

@router.get("/{user_id}")
async def get_user_by_id(
    user_id: int,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
            headers={"X-Error-Code": "USER_NOT_FOUND"},
        )

    # Scoping check: Manager can only access users within own garage
    if current_user.role == UserRole.MANAGER and user.garage_id != current_user.garage_id:
        raise HTTPException(
            status_code=404,
            detail="User not found",
            headers={"X-Error-Code": "USER_NOT_FOUND"},
        )
    # Owner can only access users within own fleet
    if current_user.role == UserRole.OWNER and user.owner_id != current_user.owner_id:
        raise HTTPException(
            status_code=404,
            detail="User not found",
            headers={"X-Error-Code": "USER_NOT_FOUND"},
        )

    return {"data": UserResponse.model_validate(user).model_dump(mode="json")}

@router.patch("/{user_id}/deactivate")
async def deactivate_user(
    user_id: int,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
):
    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(user_id)
    if not user or (current_user.role == UserRole.MANAGER and user.garage_id != current_user.garage_id) or (current_user.role == UserRole.OWNER and user.owner_id != current_user.owner_id):
        raise HTTPException(
            status_code=404,
            detail="User not found",
            headers={"X-Error-Code": "USER_NOT_FOUND"},
        )
    deactivated = await user_service.deactivate_user(user_id, actor_id=current_user.id)
    return {"data": UserResponse.model_validate(deactivated).model_dump(mode="json")}

@router.patch("/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    data: UserUpdatePassword,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
):
    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(user_id)
    if not user or (current_user.role == UserRole.MANAGER and user.garage_id != current_user.garage_id) or (current_user.role == UserRole.OWNER and user.owner_id != current_user.owner_id):
        raise HTTPException(
            status_code=404,
            detail="User not found",
            headers={"X-Error-Code": "USER_NOT_FOUND"},
        )
    reset_user = await user_service.reset_password(user_id, data.new_password, actor_id=current_user.id)
    return {"data": UserResponse.model_validate(reset_user).model_dump(mode="json")}


@router.patch("/{user_id}")
async def update_user(
    user_id: int,
    data: UserUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
):
    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(user_id)
    if not user or (current_user.role == UserRole.MANAGER and user.garage_id != current_user.garage_id) or (current_user.role == UserRole.OWNER and user.owner_id != current_user.owner_id):
        raise HTTPException(
            status_code=404,
            detail="User not found",
            headers={"X-Error-Code": "USER_NOT_FOUND"},
        )
    updated = await user_service.update_user(user_id, data, actor_id=current_user.id)
    return {"data": UserResponse.model_validate(updated).model_dump(mode="json")}

