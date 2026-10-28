from datetime import date, datetime
from typing import Literal
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, or_, case
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import require_admin
from models import (
    AuditLog,
    ParkingSession,
    SessionStatus,
    Shift,
    User,
)
from repositories import (
    AdminShiftRepository,
    ParkingSessionRepository,
    ReportRepository,
    ShiftRepository,
    SubscriptionRepository,
)
from schemas import (
    AdminSessionDetail,
    AuditLogResponse,
    ForceCloseShiftRequest,
    PaginatedResponse,
    ReportFilters,
    SessionResponse,
    SessionAdminOverrideCloseRequest,
    ShiftFilters,
    ShiftResponse,
    ShiftSummaryResponse,
)
from services import (
    AuditService,
    CardService,
    PlateService,
    PricingService,
    ReportService,
    SessionService,
    ShiftService,
    SubscriptionService,
)
from services.exceptions import (
    ShiftAlreadyClosedError,
    ShiftNotFoundError,
    SessionNotFoundError,
    SessionNotActiveError,
)
from utils.csv_export import (
    generate_sessions_csv,
    generate_monthly_revenue_csv,
    generate_daily_revenue_csv,
    generate_operator_revenue_csv,
)
from utils.time import cairo_date_str, cairo_now


router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


# ---------------------------------------------------------------------------
# Filter Dependencies
# ---------------------------------------------------------------------------


def get_report_filters(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    gate_number: int | None = Query(None, ge=1, le=5),
    operator_id: int | None = Query(None),
    status: SessionStatus | None = Query(None),
    card_code: str | None = Query(None, max_length=50),
    plate_number: str | None = Query(None, max_length=30),
    long_stay: bool = Query(False),
) -> ReportFilters:
    """Dependency that parses query parameters into a validated ReportFilters model."""
    try:
        return ReportFilters(
            start_date=start_date,
            end_date=end_date,
            gate_number=gate_number,
            operator_id=operator_id,
            status=status,
            card_code=card_code,
            plate_number=plate_number,
            long_stay=long_stay,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail=str(e),
            headers={"X-Error-Code": "INVALID_DATE_RANGE"},
        )


def get_shift_filters(
    operator_id: int | None = Query(None),
    gate_number: int | None = Query(None, ge=1, le=5),
    status: Literal["open", "closed"] | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    overdue: bool = Query(False),
) -> ShiftFilters:
    """Dependency that parses query parameters into a validated ShiftFilters model."""
    try:
        return ShiftFilters(
            operator_id=operator_id,
            gate_number=gate_number,
            status=status,
            start_date=start_date,
            end_date=end_date,
            overdue=overdue,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail=str(e),
            headers={"X-Error-Code": "INVALID_DATE_RANGE"},
        )


# ---------------------------------------------------------------------------
# Task 7.1: Live Stats
# ---------------------------------------------------------------------------


@router.get("/stats/live")
async def get_live_stats(db: AsyncSession = Depends(get_db)):
    repo = ReportRepository(db)
    service = ReportService(db, repo)
    stats = await service.get_live_stats()
    return {"data": stats.model_dump()}


# ---------------------------------------------------------------------------
# Task 7.2: Gate Panel
# ---------------------------------------------------------------------------


@router.get("/stats/gates")
async def get_gate_panel(db: AsyncSession = Depends(get_db)):
    repo = ReportRepository(db)
    service = ReportService(db, repo)
    gates = await service.get_gate_panel()
    return {"data": [g.model_dump() for g in gates]}


# ---------------------------------------------------------------------------
# Task 7.5: Sessions CSV Export (Must precede /sessions/{session_id})
# ---------------------------------------------------------------------------


@router.get("/sessions/export/csv")
async def export_sessions_csv(
    filters: ReportFilters = Depends(get_report_filters),
    db: AsyncSession = Depends(get_db),
):
    repo = ReportRepository(db)
    sessions_iter = repo.get_sessions_for_export(filters)

    users_result = await db.execute(select(User.id, User.full_name))
    operator_names = {u.id: u.full_name for u in users_result.fetchall()}

    generator = generate_sessions_csv(sessions_iter, operator_names)
    filename = f"pgms_sessions_{cairo_date_str(datetime.utcnow())}.csv"
    return StreamingResponse(
        generator,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Task 7.3: Sessions Filtered List
# ---------------------------------------------------------------------------


@router.get("/sessions", response_model=PaginatedResponse[SessionResponse])
async def get_sessions(
    filters: ReportFilters = Depends(get_report_filters),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    repo = ReportRepository(db)
    service = ReportService(db, repo)
    sessions, total = await service.get_sessions_filtered(filters, page, size)
    return PaginatedResponse(
        data=[SessionResponse.model_validate(s) for s in sessions],
        total=total,
        page=page,
        size=size,
    )


# ---------------------------------------------------------------------------
# Task 7.4: Session Detail
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}")
async def get_session_detail(
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    session_repo = ParkingSessionRepository(db)
    session = await session_repo.get_by_id(session_id)
    if not session:
        raise HTTPException(
            status_code=404,
            detail="Session not found",
            headers={"X-Error-Code": "SESSION_NOT_FOUND"},
        )

    logs_result = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.entity_type == "parking_session",
            AuditLog.entity_id == session_id,
        )
        .order_by(AuditLog.created_at.asc())
    )
    logs = logs_result.scalars().all()
    audit_responses = [AuditLogResponse.model_validate(log) for log in logs]

    detail = AdminSessionDetail(
        id=session.id,
        card_id=session.card_id,
        card_code=session.card_code,
        status=session.status,
        gate_number=session.gate_number,
        shift_id=session.shift_id,
        operator_id=session.operator_id,
        entry_time=session.entry_time,
        exit_time=session.exit_time,
        plate_number=session.plate_number,
        duration_minutes=session.duration_minutes,
        pricing_rule_id=session.pricing_rule_id,
        amount_charged=session.amount_charged,
        is_lost_card=session.is_lost_card,
        lost_card_penalty_applied=session.lost_card_penalty_applied,
        payment_method=session.payment_method,
        is_paid=session.is_paid,
        exit_operator_id=session.exit_operator_id,
        exit_shift_id=session.exit_shift_id,
        receipt_printed_at=session.receipt_printed_at,
        admin_override_by=session.admin_override_by,
        admin_override_note=session.admin_override_note,
        notes=session.notes,
        created_at=session.created_at,
        audit_logs=audit_responses,
    )
    return {"data": detail.model_dump()}


@router.post("/sessions/{session_id}/override-close")
async def admin_override_close_session(
    session_id: int,
    payload: SessionAdminOverrideCloseRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    card_service = CardService(db)
    session_repo = ParkingSessionRepository(db)
    pricing_service = PricingService(db)
    audit_service = AuditService(db)
    shift_service = ShiftService(db, audit_service)
    plate_service = PlateService()

    session_service = SessionService(
        db=db,
        card_service=card_service,
        session_repo=session_repo,
        pricing_service=pricing_service,
        shift_service=shift_service,
        audit_service=audit_service,
        plate_service=plate_service,
    )

    try:
        session = await session_service.admin_override_close_session(
            session_id=session_id,
            admin_id=current_user.id,
            amount_charged_egp=payload.amount_charged_egp,
            override_note=payload.override_note,
        )
    except SessionNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail=str(e),
            headers={"X-Error-Code": "SESSION_NOT_FOUND"},
        )
    except SessionNotActiveError as e:
        raise HTTPException(
            status_code=409,
            detail=str(e),
            headers={"X-Error-Code": "SESSION_NOT_ACTIVE"},
        )

    return {
        "success": True,
        "message": "تم إغلاق الجلسة وتحرير الكرت بنجاح",
        "data": {
            "session_id": session.id,
            "card_code": session.card_code,
            "status": session.status.value,
            "amount_charged": session.amount_charged,
            "admin_override_note": session.admin_override_note,
        },
    }



# ---------------------------------------------------------------------------
# Task 7.8: Shifts CSV Export (Must precede /shifts/{shift_id})
# ---------------------------------------------------------------------------


@router.get("/shifts/{shift_id}/export/csv")
async def export_shift_sessions_csv(
    shift_id: int,
    db: AsyncSession = Depends(get_db),
):
    shift = await db.get(Shift, shift_id)
    if not shift:
        raise HTTPException(
            status_code=404,
            detail="Shift not found",
            headers={"X-Error-Code": "SHIFT_NOT_FOUND"},
        )

    users_result = await db.execute(select(User.id, User.full_name))
    operator_names = {u.id: u.full_name for u in users_result.fetchall()}

    async def _shift_sessions_iterator():
        offset = 0
        chunk_size = 500
        while True:
            stmt = (
                select(ParkingSession)
                .where(
                    or_(
                        ParkingSession.shift_id == shift_id,
                        ParkingSession.exit_shift_id == shift_id,
                    )
                )
                .order_by(
                    case(
                        (ParkingSession.exit_shift_id == shift_id, ParkingSession.exit_time),
                        else_=ParkingSession.entry_time,
                    ).desc(),
                    ParkingSession.id.desc(),
                )
                .offset(offset)
                .limit(chunk_size)
            )
            res = await db.execute(stmt)
            chunk = res.scalars().all()
            if not chunk:
                break
            for session in chunk:
                yield session
            offset += len(chunk)
            await asyncio.sleep(0)

    generator = generate_sessions_csv(_shift_sessions_iterator(), operator_names)
    filename = f"pgms_shift_{shift_id}_{cairo_date_str(datetime.utcnow())}.csv"
    return StreamingResponse(
        generator,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Task 7.6: Shifts Filtered List
# ---------------------------------------------------------------------------


@router.get("/shifts", response_model=PaginatedResponse[ShiftResponse])
async def get_shifts(
    filters: ShiftFilters = Depends(get_shift_filters),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    admin_shift_repo = AdminShiftRepository(db)
    shifts, total = await admin_shift_repo.get_shifts_filtered(filters, page, size)

    shift_ids = [s.id for s in shifts]
    if shift_ids:
        await admin_shift_repo.get_shift_session_totals(shift_ids)

    return PaginatedResponse(
        data=[ShiftResponse.model_validate(s) for s in shifts],
        total=total,
        page=page,
        size=size,
    )


# ---------------------------------------------------------------------------
# Task 7.7: Shift Detail
# ---------------------------------------------------------------------------


@router.get("/shifts/{shift_id}")
async def get_shift_detail(
    shift_id: int,
    db: AsyncSession = Depends(get_db),
):
    shift_repo = ShiftRepository(db)
    shift = await shift_repo.get_by_id(shift_id)
    if not shift:
        raise HTTPException(
            status_code=404,
            detail="Shift not found",
            headers={"X-Error-Code": "SHIFT_NOT_FOUND"},
        )

    shift_service = ShiftService(
        db=db,
        audit_service=AuditService(db),
    )
    summary = await shift_service._compute_summary(shift, shift.closing_cash_egp)

    session_repo = ParkingSessionRepository(db)
    sessions, total = await session_repo.get_by_shift(shift_id, page=1, size=10)

    admin_shift_repo = AdminShiftRepository(db)
    shift_totals = await admin_shift_repo.get_shift_session_totals([shift_id])
    session_total = shift_totals.get(shift_id, 0)

    return {
        "data": {
            "shift": ShiftResponse.model_validate(shift).model_dump(),
            "summary": ShiftSummaryResponse.model_validate(summary).model_dump(),
            "sessions": [
                SessionResponse.model_validate(s).model_dump() for s in sessions
            ],
            "session_total": session_total,
        }
    }


# ---------------------------------------------------------------------------
# Task 7.9: Force Close Shift
# ---------------------------------------------------------------------------


@router.patch("/shifts/{shift_id}/force-close")
async def force_close_shift(
    shift_id: int,
    data: ForceCloseShiftRequest | None = None,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    shift_service = ShiftService(
        db=db,
        audit_service=AuditService(db),
    )
    closing_cash = data.closing_cash_egp if data else None
    admin_note = data.admin_note if data else None

    try:
        summary = await shift_service.force_close_shift(
            shift_id=shift_id,
            admin_id=current_user.id,
            closing_cash_piastres=closing_cash,
            admin_note=admin_note,
        )
    except ShiftNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Shift not found",
            headers={"X-Error-Code": "SHIFT_NOT_FOUND"},
        )
    except ShiftAlreadyClosedError:
        raise HTTPException(
            status_code=409,
            detail="Shift is already closed",
            headers={"X-Error-Code": "SHIFT_ALREADY_CLOSED"},
        )

    return {"data": ShiftSummaryResponse.model_validate(summary).model_dump()}


# ---------------------------------------------------------------------------
# Task 7.11: Report CSV Export (Must precede /reports/revenue)
# ---------------------------------------------------------------------------


@router.get("/reports/export/csv")
async def export_report_csv(
    report_type: str | None = Query(None, alias="type"),
    group_by: str | None = Query(None),
    filters: ReportFilters = Depends(get_report_filters),
    db: AsyncSession = Depends(get_db),
):
    export_mode = (report_type or group_by or "detailed").lower().strip()
    repo = ReportRepository(db)
    service = ReportService(db, repo)

    today = cairo_now().date()
    start_str = filters.start_date.isoformat() if filters.start_date else "all"
    end_str = filters.end_date.isoformat() if filters.end_date else "all"

    if export_mode in ("monthly", "month", "شهر", "شهري"):
        if filters.start_date is None or filters.start_date == filters.end_date:
            filters = filters.model_copy(update={
                "start_date": date(today.year, 1, 1),
                "end_date": filters.end_date or today,
            })
            start_str = filters.start_date.isoformat()
            end_str = filters.end_date.isoformat()
        monthly_data = await service.get_monthly_revenue(filters)
        generator = generate_monthly_revenue_csv(monthly_data)
        filename = f"revenue_monthly_{start_str}_{end_str}.csv"

    elif export_mode in ("daily", "day", "يوم", "يومي"):
        if filters.start_date is None:
            filters = filters.model_copy(update={
                "start_date": date(today.year, today.month, 1),
                "end_date": filters.end_date or today,
            })
            start_str = filters.start_date.isoformat()
            end_str = filters.end_date.isoformat()
        daily_data = await service.get_daily_revenue(filters)
        generator = generate_daily_revenue_csv(daily_data)
        filename = f"revenue_daily_{start_str}_{end_str}.csv"

    elif export_mode in ("operator", "worker", "operators", "workers", "عمال", "عامل"):
        if filters.start_date is None and filters.end_date is None:
            filters = filters.model_copy(update={
                "start_date": date(today.year, today.month, 1),
                "end_date": today,
            })
            start_str = filters.start_date.isoformat()
            end_str = filters.end_date.isoformat()
        operator_data = await service.get_revenue_by_operator(filters)
        generator = generate_operator_revenue_csv(operator_data)
        filename = f"revenue_by_operator_{start_str}_{end_str}.csv"

    else:
        # Default: detailed sessions
        sessions_iter = repo.get_sessions_for_export(filters)
        users_result = await db.execute(select(User.id, User.full_name))
        operator_names = {u.id: u.full_name for u in users_result.fetchall()}
        generator = generate_sessions_csv(sessions_iter, operator_names)
        filename = f"pgms_report_{start_str}_{end_str}.csv"

    return StreamingResponse(
        generator,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Task 7.10: Revenue Report
# ---------------------------------------------------------------------------


@router.get("/reports/revenue")
async def get_revenue_report(
    filters: ReportFilters = Depends(get_report_filters),
    db: AsyncSession = Depends(get_db),
):
    repo = ReportRepository(db)
    service = ReportService(db, repo)

    summary, by_gate, by_operator, daily = await asyncio.gather(
        service.get_revenue_summary(filters),
        service.get_revenue_by_gate(filters),
        service.get_revenue_by_operator(filters),
        service.get_daily_revenue(filters),
    )

    return {
        "data": {
            "summary": summary.model_dump(),
            "by_gate": [g.model_dump() for g in by_gate],
            "by_operator": [o.model_dump() for o in by_operator],
            "daily": [d.model_dump() for d in daily],
        }
    }


# ---------------------------------------------------------------------------
# Phase 4 Subscriptions Admin Endpoints (Preserved)
# ---------------------------------------------------------------------------


@router.get("/reports/subscriptions")
async def get_subscription_reports(
    start_date: date | None = None,
    end_date: date | None = None,
    plan_id: int | None = None,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get subscription revenue breakdown by plan and totals."""
    subscription_repo = SubscriptionRepository(db)
    report_service = ReportService(db)
    summary = await report_service.get_subscription_revenue_summary(
        start_date=start_date,
        end_date=end_date,
        plan_id=plan_id,
        subscription_repo=subscription_repo,
    )
    return {"data": summary.model_dump(mode="json")}


@router.get("/stats/subscriptions")
async def get_subscription_stats(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get subscription stats for admin dashboard."""
    subscription_repo = SubscriptionRepository(db)
    sub_service = SubscriptionService(
        db=db,
        subscription_repo=subscription_repo,
        plan_repo=None,
        card_service=None,
        audit_service=None,
    )
    stats = await sub_service.get_dashboard_stats()
    return {"data": stats.model_dump(mode="json")}
