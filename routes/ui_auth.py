import json
import os
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from main import templates
from config import get_settings
from database import get_db
from models.user import UserRole
from repositories.user_repo import UserRepository
from services.audit_service import AuditService
from services.auth_service import AuthService, AuthenticationError

router = APIRouter(prefix="/ui", tags=["ui-auth"])

# Load translations
translations_path = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "translations", "ar.json"
)
try:
    with open(translations_path, "r", encoding="utf-8") as f:
        translations = json.load(f)
except Exception:
    translations = {}

@router.get("/login")
async def get_login(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings = Depends(get_settings),
):
    token = request.cookies.get("pgms_token")
    if token:
        try:
            auth_service = AuthService(settings)
            payload = auth_service.decode_token(token)
            
            user_repo = UserRepository(db)
            user = await user_repo.get_by_id(int(payload.sub))
            if user and user.is_active:
                if user.role == UserRole.ADMIN:
                    return RedirectResponse("/ui/admin/dashboard", status_code=303)
                else:
                    return RedirectResponse("/ui/operator/dashboard", status_code=303)
        except Exception:
            pass

    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {"error": None},
    )

@router.post("/login")
async def post_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    settings = Depends(get_settings),
):
    auth_service = AuthService(settings)
    user_repo = UserRepository(db)
    audit_service = AuditService(db)

    try:
        user = await auth_service.authenticate_user(username, password, user_repo)
    except AuthenticationError:
        error_msg = translations.get(
            "login.error_invalid_credentials",
            "اسم المستخدم أو كلمة المرور غير صحيحة.",
        )
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"error": error_msg},
        )

    token = auth_service.create_access_token(user.id, user.role.value)
    
    if user.must_change_password:
        dest = "/ui/auth/change-password"
    elif user.role in (UserRole.ADMIN, UserRole.MANAGER, UserRole.SUPER_ADMIN):
        dest = "/ui/admin/dashboard"
    elif user.role == UserRole.OWNER:
        dest = "/ui/owner/dashboard"
    else:
        dest = "/ui/operator/dashboard"

    response = RedirectResponse(dest, status_code=303)
    
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

@router.get("/auth/change-password")
async def get_change_password(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings = Depends(get_settings),
):
    token = request.cookies.get("pgms_token")
    if not token:
        return RedirectResponse("/ui/login", status_code=303)
    try:
        auth_service = AuthService(settings)
        payload = auth_service.decode_token(token)
        user_repo = UserRepository(db)
        user = await user_repo.get_by_id(int(payload.sub))
        if not user or not user.is_active:
            return RedirectResponse("/ui/login", status_code=303)
    except Exception:
        return RedirectResponse("/ui/login", status_code=303)

    return templates.TemplateResponse(
        request,
        "auth/change_password.html",
        {"error": None, "success": None},
    )

@router.post("/auth/change-password")
async def post_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    settings = Depends(get_settings),
):
    token = request.cookies.get("pgms_token")
    if not token:
        return RedirectResponse("/ui/login", status_code=303)

    auth_service = AuthService(settings)
    try:
        payload = auth_service.decode_token(token)
        user_repo = UserRepository(db)
        user = await user_repo.get_by_id(int(payload.sub))
        if not user or not user.is_active:
            return RedirectResponse("/ui/login", status_code=303)
    except Exception:
        return RedirectResponse("/ui/login", status_code=303)

    if new_password != confirm_password:
        return templates.TemplateResponse(
            request,
            "auth/change_password.html",
            {"error": "كلمة المرور الجديدة غير متطابقة مع التأكيد.", "success": None},
        )

    if len(new_password) < 4:
        return templates.TemplateResponse(
            request,
            "auth/change_password.html",
            {"error": "يجب أن تتكون كلمة المرور من 4 خانات على الأقل.", "success": None},
        )

    if not auth_service.verify_password(current_password, user.hashed_password):
        return templates.TemplateResponse(
            request,
            "auth/change_password.html",
            {"error": "كلمة المرور الحالية غير صحيحة.", "success": None},
        )

    user.hashed_password = auth_service.hash_password(new_password)
    user.must_change_password = False

    audit_service = AuditService(db)
    await audit_service.log(
        actor_id=user.id,
        action="PASSWORD_CHANGED",
        entity_type="user",
        entity_id=user.id,
        before=None,
        after={"must_change_password": False},
        actor_type="user",
        garage_id=user.garage_id,
        owner_id=user.owner_id,
    )
    await db.commit()

    if user.role in (UserRole.ADMIN, UserRole.MANAGER, UserRole.SUPER_ADMIN):
        dest = "/ui/admin/dashboard"
    elif user.role == UserRole.OWNER:
        dest = "/ui/owner/dashboard"
    else:
        dest = "/ui/operator/dashboard"

    return RedirectResponse(dest, status_code=303)

@router.post("/logout")
async def post_logout(request: Request):
    response = RedirectResponse("/ui/login", status_code=303)
    response.set_cookie(
        key="pgms_token",
        value="",
        max_age=0,
        httponly=True,
        samesite="strict",
    )
    return response


