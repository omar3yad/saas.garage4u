from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from dependencies import get_current_user, require_any_role
from models.user import User, UserRole
from repositories.user_repo import UserRepository
from schemas.auth import LoginResponse, ChangePasswordRequest
from schemas.user import UserResponse
from services.audit_service import AuditService
from services.auth_service import AuthService, AuthenticationError

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

@router.post("/login")
async def login(
    username: str = Form(...),
    password: str = Form(...),
    garage_code: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    settings = Depends(get_settings),
):
    auth_service = AuthService(settings)
    user_repo = UserRepository(db)
    audit_service = AuditService(db)

    try:
        user = await auth_service.authenticate_user(username, password, user_repo, garage_code=garage_code)
    except AuthenticationError:
        # Task 6.7: Log failed login attempt under platform context
        try:
            db.info["is_super_admin"] = False
            db.info["is_system"] = True
            await audit_service.log(
                actor_id=None,
                action="LOGIN_FAILED",
                entity_type="platform",
                entity_id=0,
                before=None,
                after={"username": username, "garage_code": garage_code},
                actor_type="system",
                garage_id=None,
                owner_id=None,
            )
            await db.commit()
        except Exception:
            pass

        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid credentials", "code": "INVALID_CREDENTIALS"},
        )

    # Task 6.7: Critical Sequence: set transaction context before audit log
    db.info["garage_id"] = user.garage_id
    db.info["owner_id"] = user.owner_id
    db.info["is_super_admin"] = (user.role == UserRole.SUPER_ADMIN)
    db.info["is_system"] = False

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
                "gid": str(user.garage_id) if user.garage_id is not None else "",
                "oid": str(user.owner_id) if user.owner_id is not None else "",
                "isa": "true" if user.role == UserRole.SUPER_ADMIN else "false",
                "sys": "false",
            }
        )

    token = auth_service.create_access_token(user.id, user.role.value)
    
    login_resp = LoginResponse(
        user_id=user.id,
        role=user.role,
        full_name=user.full_name,
    )
    
    response = JSONResponse(content={"data": login_resp.model_dump(mode="json")})
    
    secure_cookie = settings.ENVIRONMENT in ("staging", "production")
    response.set_cookie(
        key="pgms_token",
        value=token,
        max_age=settings.JWT_EXPIRE_HOURS * 3600,
        httponly=True,
        samesite="strict",
        secure=secure_cookie,
    )

    await audit_service.log(
        actor_id=user.id,
        action="USER_LOGIN",
        entity_type="user",
        entity_id=user.id,
        before=None,
        after={"username": user.username, "role": user.role.value if hasattr(user.role, "value") else str(user.role)},
        actor_type="user",
        garage_id=user.garage_id,
        owner_id=user.owner_id,
    )
    
    await db.commit()
    return response

@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    settings = Depends(get_settings),
):
    """Task 6.5: Change password endpoint for mandatory or voluntary password changes."""
    auth_service = AuthService(settings)
    if not auth_service.verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=400,
            detail="Current password is incorrect",
            headers={"X-Error-Code": "INVALID_CREDENTIALS"},
        )

    current_user.hashed_password = auth_service.hash_password(body.new_password)
    current_user.must_change_password = False

    audit_service = AuditService(db)
    await audit_service.log(
        actor_id=current_user.id,
        action="PASSWORD_CHANGED",
        entity_type="user",
        entity_id=current_user.id,
        before=None,
        after={"must_change_password": False},
        actor_type="user",
        garage_id=current_user.garage_id,
        owner_id=current_user.owner_id,
    )
    await db.commit()
    return {"data": "Password changed successfully"}

@router.post("/logout")
async def logout():
    response = JSONResponse(content={"data": "logged out"})
    response.set_cookie(
        key="pgms_token",
        value="",
        max_age=0,
        httponly=True,
        samesite="strict",
    )
    return response

@router.get("/me")
async def get_me(current_user: User = Depends(require_any_role)):
    return {"data": UserResponse.model_validate(current_user).model_dump(mode="json")}
