import asyncio
from datetime import date
from typing import Annotated, Literal, Optional

from pydantic import BeforeValidator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from models import (
    AuditLog,
    ParkingSession,
    PricingRule,
    SessionStatus,
    Shift,
    User,
    UserRole,
)
from repositories import (
    AdminShiftRepository,
    GarageGateRepository,
    GarageRepository,
    ParkingSessionRepository,
    PricingRuleRepository,
    ReportRepository,
    ShiftRepository,
    SubscriptionRepository,
    UserRepository,
)
from schemas import ReportFilters, ShiftFilters
from services import (
    AuditService,
    AuthService,
    ReportService,
    ShiftService,
    SubscriptionService,
)
from utils.time import cairo_now

router = APIRouter(prefix="/ui/admin", tags=["ui-admin"])
owner_ui_router = APIRouter(prefix="/ui/owner", tags=["ui-owner"])


# ---------------------------------------------------------------------------
# Module-level Admin UI Auth Dependency (Redirects to /ui/login on 401/403)
# ---------------------------------------------------------------------------


async def require_admin_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
) -> User:
    """Enforces admin authorization for UI routes.

    Redirects unauthenticated or non-admin users to
    /ui/login?next={path} with status 303.
    """
    token = request.cookies.get("pgms_token")
    if not token:
        raise HTTPException(
            status_code=303,
            headers={"Location": f"/ui/login?next={request.url.path}"},
        )
    auth_service = AuthService(settings)
    try:
        payload = auth_service.decode_token(token)
        user_id = int(payload.sub)
    except Exception:
        raise HTTPException(
            status_code=303,
            headers={"Location": f"/ui/login?next={request.url.path}"},
        )
    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(user_id)
    allowed_roles = (
        UserRole.SUPER_ADMIN,
        UserRole.OWNER,
        UserRole.MANAGER,
        UserRole.ADMIN,
    )
    if not user or not user.is_active or user.role not in allowed_roles:
        raise HTTPException(
            status_code=303,
            headers={"Location": f"/ui/login?next={request.url.path}"},
        )
    return user


async def get_ui_context(
    request: Request,
    current_user: User,
    db: AsyncSession,
) -> dict:
    """Helper to populate multi-tenant context into UI templates."""
    garage_repo = GarageRepository(db)
    gate_repo = GarageGateRepository(db)
    owned_garages = []
    active_garage = None
    garage_id = None

    if current_user.role == UserRole.OWNER and current_user.owner_id:
        owned_garages = await garage_repo.get_by_owner(current_user.owner_id)
        cookie_gid = request.cookies.get("pgms_active_garage")
        if cookie_gid:
            try:
                gid = int(cookie_gid)
                for g in owned_garages:
                    if g.id == gid:
                        active_garage = g
                        garage_id = g.id
                        break
            except Exception:
                pass
        if not active_garage and owned_garages:
            active_garage = owned_garages[0]
            garage_id = active_garage.id
    elif current_user.garage_id:
        garage_id = current_user.garage_id
        active_garage = await garage_repo.get_by_id(garage_id)
    elif current_user.role == UserRole.SUPER_ADMIN:
        cookie_gid = request.cookies.get("pgms_active_garage")
        if cookie_gid:
            try:
                garage_id = int(cookie_gid)
                active_garage = await garage_repo.get_by_id(garage_id)
            except Exception:
                pass

    garage_gates = []
    if garage_id:
        garage_gates = await gate_repo.get_by_garage(garage_id)

    csrf_token = (
        getattr(request.state, "csrf_token", "")
        or request.cookies.get("pgms_csrf_token", "")
    )

    return {
        "request": request,
        "user": current_user,
        "owned_garages": owned_garages,
        "active_garage": active_garage,
        "garage_gates": garage_gates,
        "csrf_token": csrf_token,
    }


# ---------------------------------------------------------------------------
# Task 10.2: Switch Garage Endpoint (Protected with CSRF)
# ---------------------------------------------------------------------------


@router.post("/switch-garage")
async def switch_garage(
    request: Request,
    current_user: User = Depends(require_admin_ui),
    db: AsyncSession = Depends(get_db),
):
    garage_id = None
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            garage_id = body.get("garage_id")
        except Exception:
            pass
    if garage_id is None:
        try:
            form = await request.form()
            garage_id = form.get("garage_id")
        except Exception:
            pass
    if garage_id is None:
        garage_id = request.query_params.get("garage_id")

    if garage_id is None:
        raise HTTPException(
            status_code=400,
            detail="garage_id is required",
            headers={"X-Error-Code": "BAD_REQUEST"},
        )

    try:
        garage_id = int(garage_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="Invalid garage_id",
            headers={"X-Error-Code": "BAD_REQUEST"},
        )

    garage_repo = GarageRepository(db)
    if current_user.role == UserRole.OWNER:
        garage = await garage_repo.get_by_id(garage_id, owner_id=current_user.owner_id)
        if not garage:
            raise HTTPException(
                status_code=404,
                detail="Garage not found",
                headers={"X-Error-Code": "TENANT_NOT_FOUND"},
            )
    elif current_user.role == UserRole.SUPER_ADMIN:
        garage = await garage_repo.get_by_id(garage_id)
        if not garage:
            raise HTTPException(
                status_code=404,
                detail="Garage not found",
                headers={"X-Error-Code": "TENANT_NOT_FOUND"},
            )
    elif current_user.role in (UserRole.MANAGER, UserRole.ADMIN):
        if garage_id != current_user.garage_id:
            raise HTTPException(
                status_code=404,
                detail="Garage not found",
                headers={"X-Error-Code": "TENANT_NOT_FOUND"},
            )

    referer = request.headers.get("referer") or "/ui/admin/dashboard"
    response = RedirectResponse(url=referer, status_code=303)
    response.set_cookie(
        key="pgms_active_garage",
        value=str(garage_id),
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


# ---------------------------------------------------------------------------
# Task 8.2: Admin Dashboard Page
# ---------------------------------------------------------------------------


@router.get("/dashboard")
async def admin_dashboard(
    request: Request,
    current_user: User = Depends(require_admin_ui),
    db: AsyncSession = Depends(get_db),
):
    report_repo = ReportRepository(db)
    report_svc = ReportService(db, report_repo)

    today = cairo_now().date()
    admin_shift_repo = AdminShiftRepository(db)
    today_shift_filters = ShiftFilters(start_date=today)
    today_shifts, _ = await admin_shift_repo.get_shifts_filtered(
        today_shift_filters, page=1, size=50
    )

    shift_ids = [s.id for s in today_shifts]
    session_totals = {}
    if shift_ids:
        session_totals = await admin_shift_repo.get_shift_session_totals(shift_ids)

    op_ids = list({s.operator_id for s in today_shifts})
    operator_names = {}
    if op_ids:
        op_res = await db.execute(
            select(User.id, User.full_name).where(User.id.in_(op_ids))
        )
        operator_names = {row.id: row.full_name for row in op_res.fetchall()}

    stats, gates = await asyncio.gather(
        report_svc.get_live_stats(),
        report_svc.get_gate_panel(),
    )
    alerts = await report_svc.get_alert_counts()

    sub_stats = None
    try:
        sub_repo = SubscriptionRepository(db)
        sub_svc = SubscriptionService(
            db=db,
            subscription_repo=sub_repo,
            plan_repo=None,
            card_service=None,
            audit_service=None,
        )
        sub_stats = await sub_svc.get_dashboard_stats()
    except Exception:
        pass

    ui_ctx = await get_ui_context(request, current_user, db)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            **ui_ctx,
            "stats": stats,
            "gates": gates,
            "today_shifts": today_shifts,
            "session_totals": session_totals,
            "operator_names": operator_names,
            "long_stay_count": alerts["long_stay"],
            "overdue_shift_count": alerts["overdue_shifts"],
            "sub_stats": sub_stats,
        },
    )


# ---------------------------------------------------------------------------
# Task 8.3: Shifts List Page
# ---------------------------------------------------------------------------


def _empty_to_none(v: str | None) -> str | None:
    """Coerce empty-string query params (from HTML forms) to None."""
    if v == "":
        return None
    return v


_OptStr = Annotated[Optional[str], BeforeValidator(_empty_to_none)]


@router.get("/shifts")
async def admin_shifts_page(
    request: Request,
    operator_id: _OptStr = Query(None),
    gate_number: _OptStr = Query(None),
    status: _OptStr = Query(None),
    start_date: _OptStr = Query(None),
    end_date: _OptStr = Query(None),
    overdue: bool = Query(False),
    page: int = Query(1, ge=1),
    size: int = Query(100, ge=1, le=200),
    current_user: User = Depends(require_admin_ui),
    db: AsyncSession = Depends(get_db),
):
    try:
        filters = ShiftFilters(
            operator_id=int(operator_id) if operator_id else None,
            gate_number=int(gate_number) if gate_number else None,
            status=status if status in ("open", "closed") else None,
            start_date=date.fromisoformat(start_date) if start_date else None,
            end_date=date.fromisoformat(end_date) if end_date else None,
            overdue=overdue,
        )
    except (ValueError, TypeError):
        filters = ShiftFilters()

    today = cairo_now().date()
    admin_shift_repo = AdminShiftRepository(db)
    shifts, total = await admin_shift_repo.get_shifts_filtered(filters, page, size)

    shift_ids = [s.id for s in shifts]
    session_totals = {}
    if shift_ids:
        session_totals = await admin_shift_repo.get_shift_session_totals(shift_ids)

    op_ids = list({s.operator_id for s in shifts})
    operator_names = {}
    if op_ids:
        op_res = await db.execute(
            select(User.id, User.full_name).where(User.id.in_(op_ids))
        )
        operator_names = {row.id: row.full_name for row in op_res.fetchall()}

    all_operators_res = await db.execute(
        select(User).where(User.role == UserRole.OPERATOR)
    )
    all_operators = all_operators_res.scalars().all()

    ui_ctx = await get_ui_context(request, current_user, db)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "admin/shifts.html",
        {
            **ui_ctx,
            "shifts": shifts,
            "total": total,
            "page": page,
            "size": size,
            "filters": filters,
            "today": today,
            "operator_names": operator_names,
            "session_totals": session_totals,
            "all_operators": all_operators,
        },
    )


# ---------------------------------------------------------------------------
# Task 8.4: Shift Detail Page
# ---------------------------------------------------------------------------


@router.get("/shifts/{shift_id}")
async def admin_shift_detail_page(
    shift_id: int,
    request: Request,
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    current_user: User = Depends(require_admin_ui),
    db: AsyncSession = Depends(get_db),
):
    shift_repo = ShiftRepository(db)
    shift = await shift_repo.get_by_id(shift_id)
    if not shift:
        return RedirectResponse("/ui/admin/shifts", status_code=303)

    shift_svc = ShiftService(db, AuditService(db))
    summary = await shift_svc._compute_summary(shift, shift.closing_cash_egp)

    session_repo = ParkingSessionRepository(db)
    sessions, total_sessions = await session_repo.get_by_shift(
        shift_id, page=page, size=size
    )

    op_ids = list(
        {s.operator_id for s in sessions if s.operator_id}
        | {s.exit_operator_id for s in sessions if s.exit_operator_id}
    )
    operator_names = {}
    if op_ids:
        op_res = await db.execute(
            select(User.id, User.full_name).where(User.id.in_(op_ids))
        )
        operator_names = {row.id: row.full_name for row in op_res.fetchall()}

    operator = await db.get(User, shift.operator_id)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "admin/shift_detail.html",
        {
            "request": request,
            "user": current_user,
            "shift": shift,
            "operator": operator,
            "summary": summary,
            "sessions": sessions,
            "total_sessions": total_sessions,
            "operator_names": operator_names,
            "page": page,
            "size": size,
        },
    )


# ---------------------------------------------------------------------------
# Task 8.5: Sessions List Page
# ---------------------------------------------------------------------------


@router.get("/sessions")
async def admin_sessions_page(
    request: Request,
    start_date: _OptStr = Query(None),
    end_date: _OptStr = Query(None),
    gate_number: _OptStr = Query(None),
    operator_id: _OptStr = Query(None),
    status: _OptStr = Query(None),
    card_code: str | None = Query(None, max_length=50),
    plate_number: str | None = Query(None, max_length=30),
    long_stay: bool = Query(False),
    page: int = Query(1, ge=1),
    size: int = Query(100, ge=1, le=200),
    current_user: User = Depends(require_admin_ui),
    db: AsyncSession = Depends(get_db),
):
    try:
        if status:
            val = status.value if hasattr(status, "value") else str(status)
            if val.startswith("SessionStatus."):
                val = val.split(".", 1)[1]
            parsed_status = SessionStatus(val)
        else:
            parsed_status = None
    except ValueError:
        parsed_status = None

    today = cairo_now().date()
    try:
        filters = ReportFilters(
            start_date=date.fromisoformat(start_date) if start_date else None,
            end_date=date.fromisoformat(end_date) if end_date else None,
            gate_number=int(gate_number) if gate_number else None,
            operator_id=int(operator_id) if operator_id else None,
            status=parsed_status,
            card_code=card_code or None,
            plate_number=plate_number or None,
            long_stay=long_stay,
        )
    except (ValueError, TypeError):
        filters = ReportFilters()

    report_repo = ReportRepository(db)
    report_svc = ReportService(db, report_repo)
    sessions, total = await report_svc.get_sessions_filtered(filters, page, size)

    op_ids = list(
        {s.operator_id for s in sessions if s.operator_id}
        | {s.exit_operator_id for s in sessions if s.exit_operator_id}
    )
    operator_names = {}
    if op_ids:
        op_res = await db.execute(
            select(User.id, User.full_name).where(User.id.in_(op_ids))
        )
        operator_names = {row.id: row.full_name for row in op_res.fetchall()}

    all_operators_res = await db.execute(
        select(User).where(User.role == UserRole.OPERATOR)
    )
    all_operators = all_operators_res.scalars().all()

    ui_ctx = await get_ui_context(request, current_user, db)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "admin/sessions.html",
        {
            **ui_ctx,
            "sessions": sessions,
            "total": total,
            "page": page,
            "size": size,
            "filters": filters,
            "today": today,
            "operator_names": operator_names,
            "all_operators": all_operators,
        },
    )


# ---------------------------------------------------------------------------
# Task 8.6: Session Detail Page
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}")
async def admin_session_detail_page(
    session_id: int,
    request: Request,
    current_user: User = Depends(require_admin_ui),
    db: AsyncSession = Depends(get_db),
):
    session_repo = ParkingSessionRepository(db)
    session = await session_repo.get_by_id(session_id)
    if not session:
        return RedirectResponse("/ui/admin/sessions", status_code=303)

    logs_result = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.entity_type == "parking_session",
            AuditLog.entity_id == session_id,
        )
        .order_by(AuditLog.created_at.asc())
    )
    audit_logs = logs_result.scalars().all()

    operator = await db.get(User, session.operator_id) if session.operator_id else None
    exit_operator = (
        await db.get(User, session.exit_operator_id)
        if session.exit_operator_id
        else None
    )
    pricing_rule = (
        await db.get(PricingRule, session.pricing_rule_id)
        if session.pricing_rule_id
        else None
    )

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "admin/session_detail.html",
        {
            "request": request,
            "user": current_user,
            "session": session,
            "operator": operator,
            "exit_operator": exit_operator,
            "pricing_rule": pricing_rule,
            "audit_logs": audit_logs,
        },
    )


@router.get("/sessions/{session_id}/plate-image")
async def admin_session_plate_image(
    session_id: int,
    current_user: User = Depends(require_admin_ui),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
):
    from services.plate_capture_service import PlateCaptureService
    session_repo = ParkingSessionRepository(db)
    session = await session_repo.get_by_id(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="الجلسة غير موجودة")
    path = PlateCaptureService(db, settings).resolve_image(session.plate_image_path)
    if path is None:
        raise HTTPException(status_code=404, detail="صورة اللوحة غير موجودة")
    media_type = "image/jpeg" if path.endswith((".jpg", ".jpeg")) else "image/webp"
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=86400"})


@router.get("/sessions/{session_id}/exit-image")
async def admin_session_exit_image(
    session_id: int,
    current_user: User = Depends(require_admin_ui),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
):
    from services.plate_capture_service import PlateCaptureService
    session_repo = ParkingSessionRepository(db)
    session = await session_repo.get_by_id(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="الجلسة غير موجودة")
    path = PlateCaptureService(db, settings).resolve_image(session.exit_image_path)
    if path is None:
        raise HTTPException(status_code=404, detail="صورة خروج السيارة غير موجودة")
    media_type = "image/jpeg" if path.endswith((".jpg", ".jpeg")) else "image/webp"
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=86400"})


# ---------------------------------------------------------------------------
# Task 8.7: Revenue Report Page
# ---------------------------------------------------------------------------


@router.get("/reports/revenue")
async def admin_revenue_report_page(
    request: Request,
    start_date: _OptStr = Query(None),
    end_date: _OptStr = Query(None),
    gate_number: _OptStr = Query(None),
    operator_id: _OptStr = Query(None),
    current_user: User = Depends(require_admin_ui),
    db: AsyncSession = Depends(get_db),
):
    today = cairo_now().date()
    try:
        s_date = date.fromisoformat(start_date) if start_date else today
        e_date = date.fromisoformat(end_date) if end_date else today
        filters = ReportFilters(
            start_date=s_date,
            end_date=e_date,
            gate_number=int(gate_number) if gate_number else None,
            operator_id=int(operator_id) if operator_id else None,
        )
    except (ValueError, TypeError):
        filters = ReportFilters(start_date=today, end_date=today)

    report_repo = ReportRepository(db)
    report_svc = ReportService(db, report_repo)

    summary, by_gate, by_service_type, by_operator, daily = await asyncio.gather(
        report_svc.get_revenue_summary(filters),
        report_svc.get_revenue_by_gate(filters),
        report_svc.get_revenue_by_service_type(filters),
        report_svc.get_revenue_by_operator(filters),
        report_svc.get_daily_revenue(filters),
    )

    all_operators_res = await db.execute(
        select(User).where(User.role == UserRole.OPERATOR)
    )
    all_operators = all_operators_res.scalars().all()

    ui_ctx = await get_ui_context(request, current_user, db)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "admin/reports/revenue.html",
        {
            **ui_ctx,
            "filters": filters,
            "summary": summary,
            "by_gate": by_gate,
            "by_service_type": by_service_type,
            "by_operator": by_operator,
            "daily": daily,
            "all_operators": all_operators,
        },
    )


# ---------------------------------------------------------------------------
# Task 8.8: Standalone A4 Print Report Page
# ---------------------------------------------------------------------------


@router.get("/reports/print")
async def admin_print_report_page(
    request: Request,
    report_type: str = Query(...),
    shift_id: _OptStr = Query(None),
    start_date: _OptStr = Query(None),
    end_date: _OptStr = Query(None),
    gate_number: _OptStr = Query(None),
    operator_id: _OptStr = Query(None),
    current_user: User = Depends(require_admin_ui),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
):
    if report_type not in ("revenue", "sessions", "shift"):
        raise HTTPException(
            status_code=422,
            detail="Invalid report type",
            headers={"X-Error-Code": "INVALID_REPORT_TYPE"},
        )

    parsed_shift_id = int(shift_id) if shift_id else None
    if report_type == "shift" and parsed_shift_id is None:
        raise HTTPException(
            status_code=422,
            detail="Shift ID is required for shift report",
            headers={"X-Error-Code": "SHIFT_ID_REQUIRED_FOR_PRINT"},
        )

    filters = ReportFilters(
        start_date=date.fromisoformat(start_date) if start_date else None,
        end_date=date.fromisoformat(end_date) if end_date else None,
        gate_number=int(gate_number) if gate_number else None,
        operator_id=int(operator_id) if operator_id else None,
    )

    report_repo = ReportRepository(db)
    report_svc = ReportService(db, report_repo)
    truncated = False
    report_data = {}

    if report_type == "sessions":
        sessions, total = await report_svc.get_sessions_filtered(
            filters, page=1, size=500
        )
        truncated = total > 500
        users_result = await db.execute(select(User.id, User.full_name))
        operator_names = {u.id: u.full_name for u in users_result.fetchall()}
        report_data = {
            "sessions": sessions,
            "total": total,
            "operator_names": operator_names,
        }

    elif report_type == "revenue":
        today = cairo_now().date()
        if not filters.start_date:
            filters.start_date = today
        if not filters.end_date:
            filters.end_date = today
        summary, by_gate, by_service_type, by_operator, daily = await asyncio.gather(
            report_svc.get_revenue_summary(filters),
            report_svc.get_revenue_by_gate(filters),
            report_svc.get_revenue_by_service_type(filters),
            report_svc.get_revenue_by_operator(filters),
            report_svc.get_daily_revenue(filters),
        )
        report_data = {
            "summary": summary,
            "by_gate": by_gate,
            "by_service_type": by_service_type,
            "by_operator": by_operator,
            "daily": daily,
        }

    elif report_type == "shift":
        shift_repo = ShiftRepository(db)
        shift = await shift_repo.get_by_id(shift_id)
        if not shift:
            raise HTTPException(status_code=404, detail="Shift not found")
        shift_svc = ShiftService(db, AuditService(db))
        summary = await shift_svc._compute_summary(shift, shift.closing_cash_egp)
        session_repo = ParkingSessionRepository(db)
        sessions, total = await session_repo.get_by_shift(shift_id, page=1, size=500)
        truncated = total > 500
        operator = await db.get(User, shift.operator_id)
        op_ids = list(
            {s.operator_id for s in sessions if s.operator_id}
            | {s.exit_operator_id for s in sessions if s.exit_operator_id}
        )
        operator_names = {}
        if op_ids:
            op_res = await db.execute(
                select(User.id, User.full_name).where(User.id.in_(op_ids))
            )
            operator_names = {row.id: row.full_name for row in op_res.fetchall()}
        report_data = {
            "shift": shift,
            "operator": operator,
            "summary": summary,
            "sessions": sessions,
            "total_sessions": total,
            "operator_names": operator_names,
        }

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "admin/reports/print.html",
        {
            "request": request,
            "report_type": report_type,
            "data": report_data,
            "filters": filters,
            "generated_at": cairo_now(),
            "garage_name": settings.APP_NAME,
            "truncated": truncated,
        },
    )


# ---------------------------------------------------------------------------
# Task 8.9: Rates Management Page
# ---------------------------------------------------------------------------


@router.get("/rates")
async def admin_rates_page(
    request: Request,
    current_user: User = Depends(require_admin_ui),
    db: AsyncSession = Depends(get_db),
):
    pricing_repo = PricingRuleRepository(db)
    rules, total = await pricing_repo.get_all(page=1, size=100)
    rules.sort(key=lambda r: r.created_at, reverse=True)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "admin/rates.html",
        {
            "request": request,
            "user": current_user,
            "rules": rules,
        },
    )


# ---------------------------------------------------------------------------
# Task 8.10: Operators Page
# ---------------------------------------------------------------------------


@router.get("/operators")
async def admin_operators_page(
    request: Request,
    current_user: User = Depends(require_admin_ui),
    db: AsyncSession = Depends(get_db),
):
    user_repo = UserRepository(db)
    users, total = await user_repo.get_all(page=1, size=200)

    operator_data = []
    for u in users:
        active_shift_res = await db.execute(
            select(Shift)
            .where(Shift.operator_id == u.id, Shift.ended_at.is_(None))
            .limit(1)
        )
        active_shift = active_shift_res.scalars().first()

        last_shift_res = await db.execute(
            select(Shift.started_at)
            .where(Shift.operator_id == u.id)
            .order_by(Shift.started_at.desc())
            .limit(1)
        )
        last_shift_date = last_shift_res.scalars().first()

        operator_data.append(
            {
                "user": u,
                "active_shift": active_shift,
                "last_shift_date": last_shift_date,
            }
        )

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "admin/operators.html",
        {
            "request": request,
            "user": current_user,
            "operators": operator_data,
        },
    )


# ---------------------------------------------------------------------------
# Cards Management Page
# ---------------------------------------------------------------------------


@router.get("/cards")
async def admin_cards_page(
    request: Request,
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    current_user: User = Depends(require_admin_ui),
    db: AsyncSession = Depends(get_db),
):
    from repositories.card_repo import ParkingCardRepository
    from models.parking_card import CardStatus
    
    card_status = None
    if status:
        try:
            card_status = CardStatus(status.upper())
        except ValueError:
            pass

    card_repo = ParkingCardRepository(db)
    cards, total = await card_repo.get_all(status=card_status, search=search, page=page, size=30)
    counts = await card_repo.get_counts()
    
    total_pages = max(1, (total + 29) // 30)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "admin/cards.html",
        {
            "request": request,
            "user": current_user,
            "cards": cards,
            "counts": counts,
            "total": total,
            "current_page": page,
            "total_pages": total_pages,
            "selected_status": status or "",
            "search": search or "",
        },
    )


# ---------------------------------------------------------------------------
# Task 10.4: Owner Fleet Dashboard Endpoint
# ---------------------------------------------------------------------------


@owner_ui_router.get("/dashboard")
async def owner_dashboard_page(
    request: Request,
    current_user: User = Depends(require_admin_ui),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in (UserRole.OWNER, UserRole.SUPER_ADMIN):
        if current_user.role == UserRole.MANAGER:
            return RedirectResponse("/ui/admin/dashboard", status_code=303)
        return RedirectResponse("/ui/operator/dashboard", status_code=303)

    garage_repo = GarageRepository(db)
    session_repo = ParkingSessionRepository(db)

    if current_user.role == UserRole.SUPER_ADMIN:
        garages, _ = await garage_repo.get_all(page=1, size=100)
    else:
        garages = await garage_repo.get_by_owner(current_user.owner_id)

    from datetime import datetime, time
    today = cairo_now().date()
    today_start = datetime.combine(today, time.min)

    garages_stats = []
    total_active_sessions = 0
    total_revenue = 0.0

    for g in garages:
        active_count = await session_repo.count_active(g.id)
        # Revenue today
        res = await db.execute(
            select(func.coalesce(func.sum(ParkingSession.amount_charged), 0.0)).where(
                ParkingSession.garage_id == g.id,
                ParkingSession.status == SessionStatus.COMPLETED,
                ParkingSession.exit_time >= today_start,
            )
        )
        rev = float(res.scalar() or 0.0)

        cap = g.capacity or 100
        occ_rate = round((active_count / cap) * 100, 1) if cap > 0 else 0.0

        garages_stats.append({
            "garage": g,
            "active_sessions": active_count,
            "capacity": cap,
            "occupancy_rate": occ_rate,
            "today_revenue": rev,
        })
        total_active_sessions += active_count
        total_revenue += rev

    csrf_token = (
        getattr(request.state, "csrf_token", "")
        or request.cookies.get("pgms_csrf_token", "")
    )

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "owner/dashboard.html",
        {
            "request": request,
            "user": current_user,
            "garages_stats": garages_stats,
            "total_garages": len(garages),
            "total_active_sessions": total_active_sessions,
            "total_revenue": total_revenue,
            "today": today,
            "csrf_token": csrf_token,
        },
    )


@router.get("/owner/dashboard")
async def admin_owner_dashboard_redirect():
    """Redirect /ui/admin/owner/dashboard to /ui/owner/dashboard."""
    return RedirectResponse("/ui/owner/dashboard", status_code=303)

