import logging
from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db, AsyncSessionLocal
from models.user import User, UserRole
from repositories.user_repo import UserRepository
from repositories.garage_repo import GarageRepository
from services.auth_service import AuthService, AuthenticationError
from utils.tenant_context import TenantContext

logger = logging.getLogger(__name__)

EXEMPT_PASSWORD_CHANGE_PATHS = {
    "/api/v1/auth/change-password",
    "/ui/auth/change-password",
    "/api/v1/auth/logout",
    "/ui/auth/logout",
    "/ui/logout",
    "/auth/logout",
}

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings = Depends(get_settings),
) -> User:
    token = request.cookies.get("pgms_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"X-Error-Code": "UNAUTHORIZED"},
        )

    auth_service = AuthService(settings)
    try:
        payload = auth_service.decode_token(token)
    except AuthenticationError:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"X-Error-Code": "UNAUTHORIZED"},
        )

    try:
        user_id = int(payload.sub)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"X-Error-Code": "UNAUTHORIZED"},
        )

    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"X-Error-Code": "UNAUTHORIZED"},
        )

    # Task 6.5: enforce_password_change
    if user.must_change_password and request.url.path not in EXEMPT_PASSWORD_CHANGE_PATHS:
        if request.url.path.startswith("/api/"):
            raise HTTPException(
                status_code=403,
                detail="Password change required",
                headers={"X-Error-Code": "PASSWORD_CHANGE_REQUIRED"},
            )
        else:
            raise HTTPException(
                status_code=303,
                headers={"Location": "/ui/auth/change-password"},
            )

    return user

async def get_tenant_context(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TenantContext:
    garage_id: int | None = None
    owner_id: int | None = None
    is_super_admin: bool = False
    is_system: bool = False

    target_garage_id: int | None = None
    raw_path_gid = request.path_params.get("garage_id")
    if raw_path_gid is not None:
        try:
            target_garage_id = int(raw_path_gid)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=404,
                detail="Invalid garage ID",
                headers={"X-Error-Code": "TENANT_NOT_FOUND"},
            )

    garage_repo = GarageRepository(db)

    if current_user.role == UserRole.SUPER_ADMIN:
        is_super_admin = True
        garage_id = target_garage_id
        owner_id = None

        # Task 6.6: Automatically log SUPER_ADMIN_GARAGE_ACCESS in independent session
        if target_garage_id is not None:
            try:
                import json
                from repositories.audit_log_repo import AuditLogRepository
                async with AsyncSessionLocal() as audit_db:
                    audit_db.info["is_super_admin"] = True
                    audit_repo = AuditLogRepository(audit_db)
                    payload_after = json.dumps({
                        "target_garage_id": target_garage_id,
                        "endpoint": request.url.path,
                        "method": request.method,
                    })
                    await audit_repo.create(
                        actor_id=current_user.id,
                        action="SUPER_ADMIN_GARAGE_ACCESS",
                        entity_type="platform",
                        entity_id=0,
                        payload_after=payload_after,
                        actor_type="user",
                        garage_id=None,
                        owner_id=None,
                    )
                    await audit_db.commit()
            except Exception as e:
                logger.error("Failed to log super admin garage access: %s", e)

    elif current_user.role == UserRole.OWNER:
        owner_id = current_user.owner_id
        if target_garage_id is not None:
            garage_id = target_garage_id
        else:
            cookie_gid = request.cookies.get("pgms_active_garage")
            if cookie_gid:
                try:
                    garage_id = int(cookie_gid)
                except (ValueError, TypeError):
                    garage_id = None

        if garage_id is not None:
            owned_garage = await garage_repo.get_by_id(garage_id, owner_id=owner_id)
            if not owned_garage:
                raise HTTPException(
                    status_code=404,
                    detail="Garage not found",
                    headers={"X-Error-Code": "TENANT_NOT_FOUND"},
                )

    elif current_user.role in (UserRole.MANAGER, UserRole.ADMIN, UserRole.OPERATOR):
        garage_id = current_user.garage_id
        owner_id = current_user.owner_id
        if target_garage_id is not None and target_garage_id != current_user.garage_id:
            raise HTTPException(
                status_code=404,
                detail="Garage not found",
                headers={"X-Error-Code": "TENANT_NOT_FOUND"},
            )

    tenant_ctx = TenantContext(
        garage_id=garage_id,
        owner_id=owner_id,
        is_super_admin=is_super_admin,
        is_system=is_system,
        user_id=current_user.id,
        role=current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role),
    )

    # Inject into db.info for SQLAlchemy session & after_begin hook
    db.info["garage_id"] = tenant_ctx.garage_id
    db.info["owner_id"] = tenant_ctx.owner_id
    db.info["is_super_admin"] = tenant_ctx.is_super_admin
    db.info["is_system"] = tenant_ctx.is_system

    # If transaction is already active and dialect is postgresql, update transaction settings immediately
    if db.in_transaction() and db.bind and db.bind.dialect.name == "postgresql":
        await db.execute(
            text("""
                SELECT 
                    set_config('app.current_garage_id', :gid, true),
                    set_config('app.current_owner_id', :oid, true),
                    set_config('app.is_super_admin', :isa, true),
                    set_config('app.is_system_context', :sys, true);
            """),
            {
                "gid": str(tenant_ctx.garage_id) if tenant_ctx.garage_id is not None else "",
                "oid": str(tenant_ctx.owner_id) if tenant_ctx.owner_id is not None else "",
                "isa": "true" if tenant_ctx.is_super_admin else "false",
                "sys": "true" if tenant_ctx.is_system else "false",
            }
        )

    return tenant_ctx

async def require_super_admin(
    user: User = Depends(get_current_user),
) -> User:
    if user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Insufficient permissions",
            headers={"X-Error-Code": "INSUFFICIENT_PERMISSIONS"},
        )
    return user

async def require_owner_or_admin(
    user: User = Depends(get_current_user),
) -> User:
    if user.role not in (UserRole.SUPER_ADMIN, UserRole.OWNER):
        raise HTTPException(
            status_code=403,
            detail="Insufficient permissions",
            headers={"X-Error-Code": "INSUFFICIENT_PERMISSIONS"},
        )
    return user

require_owner = require_owner_or_admin

async def require_manager_or_above(
    user: User = Depends(get_current_user),
) -> User:
    if user.role not in (UserRole.SUPER_ADMIN, UserRole.OWNER, UserRole.MANAGER, UserRole.ADMIN):
        raise HTTPException(
            status_code=403,
            detail="Insufficient permissions",
            headers={"X-Error-Code": "INSUFFICIENT_PERMISSIONS"},
        )
    return user

require_manager = require_manager_or_above
require_admin = require_manager_or_above

async def require_operator_or_above(
    user: User = Depends(get_current_user),
) -> User:
    if user.role not in (
        UserRole.SUPER_ADMIN,
        UserRole.OWNER,
        UserRole.MANAGER,
        UserRole.ADMIN,
        UserRole.OPERATOR,
    ):
        raise HTTPException(
            status_code=403,
            detail="Insufficient permissions",
            headers={"X-Error-Code": "INSUFFICIENT_PERMISSIONS"},
        )
    return user

require_operator = require_operator_or_above

async def require_any_role(
    user: User = Depends(get_current_user),
) -> User:
    return user

def validate_role_assignment(
    creator: User,
    target_role: UserRole | str,
    target_garage_id: int | None = None,
    target_owner_id: int | None = None,
) -> None:
    role_val = target_role.value if hasattr(target_role, "value") else str(target_role)

    if creator.role == UserRole.SUPER_ADMIN:
        return

    if creator.role == UserRole.OWNER:
        if role_val == UserRole.SUPER_ADMIN.value:
            raise HTTPException(
                status_code=403,
                detail="Owners cannot create or assign SUPER_ADMIN role",
                headers={"X-Error-Code": "PRIVILEGE_ESCALATION_DENIED"},
            )
        if target_owner_id is not None and target_owner_id != creator.owner_id:
            raise HTTPException(
                status_code=403,
                detail="Cannot assign users to a different owner organization",
                headers={"X-Error-Code": "PRIVILEGE_ESCALATION_DENIED"},
            )
        return

    if creator.role in (UserRole.MANAGER, UserRole.ADMIN):
        if role_val in (UserRole.SUPER_ADMIN.value, UserRole.OWNER.value, UserRole.MANAGER.value, UserRole.ADMIN.value):
            raise HTTPException(
                status_code=403,
                detail="Managers cannot create or assign equal or higher roles",
                headers={"X-Error-Code": "PRIVILEGE_ESCALATION_DENIED"},
            )
        if target_garage_id is not None and target_garage_id != creator.garage_id:
            raise HTTPException(
                status_code=403,
                detail="Managers cannot assign users to a different garage",
                headers={"X-Error-Code": "PRIVILEGE_ESCALATION_DENIED"},
            )
        return

    raise HTTPException(
        status_code=403,
        detail="Operators cannot create or modify users",
        headers={"X-Error-Code": "PRIVILEGE_ESCALATION_DENIED"},
    )

__all__ = [
    "get_current_user",
    "get_tenant_context",
    "require_super_admin",
    "require_owner_or_admin",
    "require_owner",
    "require_manager_or_above",
    "require_manager",
    "require_admin",
    "require_operator_or_above",
    "require_operator",
    "require_any_role",
    "validate_role_assignment",
]
