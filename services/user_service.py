from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from models.user import User, UserRole
from repositories.user_repo import UserRepository
from schemas.user import UserCreate, UserUpdate
from services.auth_service import AuthService
from services.audit_service import AuditService

class UserService:
    def __init__(
        self,
        db: AsyncSession,
        user_repo: UserRepository,
        auth_service: AuthService,
        audit_service: AuditService,
    ):
        self.db = db
        self.user_repo = user_repo
        self.auth_service = auth_service
        self.audit_service = audit_service

    async def create_user(self, data: UserCreate) -> User:
        existing_user = await self.user_repo.get_by_username(data.username)
        if existing_user:
            raise HTTPException(
                status_code=409,
                detail="Username already exists",
                headers={"X-Error-Code": "USERNAME_ALREADY_EXISTS"},
            )

        hashed_password = self.auth_service.hash_password(data.password)
        user_dict = data.model_dump(exclude={"password"})
        new_user = await self.user_repo.create(**user_dict, hashed_password=hashed_password)

        await self.audit_service.log(
            actor_id=new_user.id,
            action="USER_CREATED",
            entity_type="user",
            entity_id=new_user.id,
            before=None,
            after=user_dict,
        )

        await self.db.commit()
        await self.db.refresh(new_user)
        return new_user

    async def deactivate_user(self, user_id: int, actor_id: int) -> User:
        if user_id == actor_id:
            raise HTTPException(
                status_code=403,
                detail="Cannot deactivate self",
                headers={"X-Error-Code": "CANNOT_DEACTIVATE_SELF"},
            )

        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found",
                headers={"X-Error-Code": "USER_NOT_FOUND"},
            )

        if not user.is_active:
            raise HTTPException(
                status_code=409,
                detail="User already inactive",
                headers={"X-Error-Code": "USER_ALREADY_INACTIVE"},
            )

        before_dict = {"is_active": user.is_active}
        await self.user_repo.update_fields(user, is_active=False)

        await self.audit_service.log(
            actor_id=actor_id,
            action="USER_DEACTIVATED",
            entity_type="user",
            entity_id=user_id,
            before=before_dict,
            after={"is_active": False},
        )

        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def reset_password(self, user_id: int, new_password: str, actor_id: int) -> User:
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found",
                headers={"X-Error-Code": "USER_NOT_FOUND"},
            )

        new_hash = self.auth_service.hash_password(new_password)
        await self.user_repo.update_fields(user, hashed_password=new_hash)

        await self.audit_service.log(
            actor_id=actor_id,
            action="USER_PASSWORD_RESET",
            entity_type="user",
            entity_id=user_id,
            before=None,
            after={"user_id": user_id},
        )

        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def update_user(
        self,
        user_id: int,
        data: UserUpdate,
        actor_id: int,
    ) -> User:
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found",
                headers={"X-Error-Code": "USER_NOT_FOUND"},
            )

        before_dict = {
            "full_name": user.full_name,
            "gate_number": user.gate_number,
            "role": user.role.value if hasattr(user.role, "value") else str(user.role),
        }

        update_data = {}
        if data.full_name is not None and data.full_name.strip():
            update_data["full_name"] = data.full_name.strip()

        target_role = data.role or user.role
        if data.role is not None:
            update_data["role"] = data.role

        if target_role == UserRole.OPERATOR:
            if data.gate_number is not None:
                update_data["gate_number"] = data.gate_number
            elif user.gate_number is None:
                update_data["gate_number"] = 1
        elif target_role == UserRole.ADMIN:
            update_data["gate_number"] = None

        if update_data:
            await self.user_repo.update_fields(user, **update_data)

            await self.audit_service.log(
                actor_id=actor_id,
                action="USER_UPDATED",
                entity_type="user",
                entity_id=user_id,
                before=before_dict,
                after=update_data,
                garage_id=user.garage_id,
                owner_id=user.owner_id,
            )

            await self.db.commit()
            await self.db.refresh(user)

        return user

    async def get_all_users(
        self,
        garage_id: int | None = None,
        owner_id: int | None = None,
        role: UserRole | None = None,
        is_active: bool | None = None,
        gate_number: int | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[User], int]:
        return await self.user_repo.get_all(
            garage_id=garage_id,
            owner_id=owner_id,
            role=role,
            is_active=is_active,
            gate_number=gate_number,
            page=page,
            size=size,
        )
