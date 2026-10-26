import asyncio
import logging
from contextlib import nullcontext
from datetime import datetime
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from utils.jinja import format_cairo_12h

from config import get_settings
from database import get_db
from dependencies import require_operator
from models.user import User, UserRole
from models.pricing_rule import PricingRule
from models.parking_card import ParkingCard, CardStatus
from models.parking_session import ParkingSession, PlateCaptureStatus, SessionStatus
from repositories.session_repo import ParkingSessionRepository
from repositories.rate_repo import PricingRuleRepository
from repositories.subscription_plan_repo import SubscriptionPlanRepository
from repositories.subscriber_repo import SubscriberRepository
from repositories.subscription_repo import SubscriptionRepository
from services.session_service import SessionService
from services.card_service import CardService
from services.pricing_service import PricingService
from services.shift_service import ShiftService
from services.audit_service import AuditService
from services.plate_service import PlateService
from services.plate_capture_service import PlateCaptureService, capture_payload
from services.subscription_service import SubscriptionService
from services.pricing_helpers import format_duration, format_egp
from schemas.receipt import ReceiptData
from utils.templates import load_translations
from services.exceptions import (
    CardNotFoundError,
    CardNotAvailableError,
    CardAlreadyActiveError,
    CardHasNoActiveSessionError,
    InvalidBarcodeFormatError,
    SessionNotFoundError,
    SessionNotActiveError,
    NoActiveShiftError,
    NoPricingRuleError,
    ShiftAlreadyOpenError,
    SubscriptionDailyLimitReachedError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ui/operator", tags=["ui-operator"])

# Long-poll safety net: re-read the DB at least this often in case a result
# notification was lost (e.g. event-bus listener reconnecting).
_PLATE_RECHECK_INTERVAL_S = 1.0

# Load translations for routing rendering errors
translations = load_translations()


def get_error_translation_key(e: Exception) -> str:
    mapping = {
        "CardNotFoundError": "errors.card_not_found",
        "CardAlreadyActiveError": "errors.card_already_active",
        "CardNotAvailableError": "errors.card_not_available",
        "CardHasNoActiveSessionError": "errors.no_active_session",
        "NoActiveShiftError": "errors.no_active_shift",
        "InvalidBarcodeFormatError": "errors.invalid_barcode_format",
        "SessionNotFoundError": "errors.session_not_found",
        "SessionNotActiveError": "errors.no_active_session",
        "NoPricingRuleError": "errors.no_pricing_rule",
        "SubscriptionDailyLimitReachedError": "errors.subscription_daily_limit",
    }
    return mapping.get(e.__class__.__name__, "errors.server_error")


def get_session_repo(db: AsyncSession = Depends(get_db)) -> ParkingSessionRepository:
    return ParkingSessionRepository(db)


def get_pricing_repo(db: AsyncSession = Depends(get_db)) -> PricingRuleRepository:
    return PricingRuleRepository(db)


def get_pricing_service(db: AsyncSession = Depends(get_db)) -> PricingService:
    return PricingService(db)


def get_shift_service(db: AsyncSession = Depends(get_db)) -> ShiftService:
    audit_service = AuditService(db)
    return ShiftService(db, audit_service)


def get_session_service(db: AsyncSession = Depends(get_db)) -> SessionService:
    card_service = CardService(db)
    session_repo = ParkingSessionRepository(db)
    pricing_service = PricingService(db)
    audit_service = AuditService(db)
    shift_service = ShiftService(db, audit_service)
    plate_service = PlateService()
    sub_repo = SubscriptionRepository(db)
    plan_repo = SubscriptionPlanRepository(db)
    sub_service = SubscriptionService(
        db=db,
        subscription_repo=sub_repo,
        plan_repo=plan_repo,
        card_service=card_service,
        audit_service=audit_service,
    )
    return SessionService(
        db=db,
        card_service=card_service,
        session_repo=session_repo,
        pricing_service=pricing_service,
        shift_service=shift_service,
        audit_service=audit_service,
        plate_service=plate_service,
        subscription_service=sub_service,
    )


@router.get("/shift/start")
async def start_shift_page(
    request: Request,
    current_user: User = Depends(require_operator),
    shift_service: ShiftService = Depends(get_shift_service),
):
    active = await shift_service.get_active_shift(current_user.id)
    if active:
        return RedirectResponse("/ui/operator/dashboard", status_code=303)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "operator/shift_start.html",
        {"request": request, "user": current_user},
    )


@router.post("/shift/start")
async def open_shift(
    request: Request,
    opening_cash_egp: int = Form(0),
    current_user: User = Depends(require_operator),
    shift_service: ShiftService = Depends(get_shift_service),
):
    gate = current_user.gate_number or 2
    if gate == 1:
        opening_cash_piastres = 0
    else:
        opening_cash_piastres = opening_cash_egp * 100
    try:
        await shift_service.open_shift(
            operator_id=current_user.id,
            gate_number=gate,
            opening_cash_egp=opening_cash_piastres,
        )
    except ShiftAlreadyOpenError:
        pass
    return RedirectResponse("/ui/operator/dashboard", status_code=303)



@router.get("/shift/end")
async def end_shift_page(
    request: Request,
    current_user: User = Depends(require_operator),
    shift_service: ShiftService = Depends(get_shift_service),
    db: AsyncSession = Depends(get_db),
):
    try:
        shift = await shift_service.require_active_shift(current_user.id)
    except NoActiveShiftError:
        return RedirectResponse("/ui/operator/dashboard", status_code=303)

    summary = await shift_service._compute_summary(shift, None)

    entry_stats = None
    if current_user.gate_number == 1:
        from sqlalchemy import func
        total_entered_res = await db.execute(
            select(func.count(ParkingSession.id)).where(ParkingSession.shift_id == shift.id)
        )
        total_entered = total_entered_res.scalar() or 0

        active_res = await db.execute(
            select(func.count(ParkingSession.id)).where(
                ParkingSession.shift_id == shift.id,
                ParkingSession.status == SessionStatus.ACTIVE,
            )
        )
        active_count = active_res.scalar() or 0
        completed_count = max(total_entered - active_count, 0)

        sub_res = await db.execute(
            select(func.count(ParkingSession.id)).where(
                ParkingSession.shift_id == shift.id,
                ParkingSession.subscription_id.isnot(None),
            )
        )
        subs_count = sub_res.scalar() or 0

        entry_stats = {
            "total_entered": total_entered,
            "active_count": active_count,
            "completed_count": completed_count,
            "subs_count": subs_count,
        }

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "operator/shift_end.html",
        {
            "request": request,
            "user": current_user,
            "shift": shift,
            "summary": summary,
            "entry_stats": entry_stats,
        },
    )


@router.post("/shift/end")
async def close_shift(
    request: Request,
    closing_cash_egp: int | None = Form(None),
    current_user: User = Depends(require_operator),
    shift_service: ShiftService = Depends(get_shift_service),
):
    try:
        shift = await shift_service.require_active_shift(current_user.id)
    except NoActiveShiftError:
        return RedirectResponse("/ui/operator/dashboard", status_code=303)

    if current_user.gate_number == 1:
        closing_cash_piastres = 0
    elif closing_cash_egp is not None:
        closing_cash_piastres = closing_cash_egp * 100
    else:
        # Exit operator without manual cash count - automatically matches computed total revenue
        summary = await shift_service._compute_summary(shift, None)
        closing_cash_piastres = summary.computed_total_piastres

    await shift_service.close_shift(
        shift_id=shift.id,
        operator_id=current_user.id,
        closing_cash_piastres=closing_cash_piastres,
    )

    response = RedirectResponse("/ui/login", status_code=303)
    response.delete_cookie("pgms_token")
    return response


@router.get("/dashboard")
async def dashboard_page(
    request: Request,
    current_user: User = Depends(require_operator),
    shift_service: ShiftService = Depends(get_shift_service),
    session_repo: ParkingSessionRepository = Depends(get_session_repo),
    pricing_repo: PricingRuleRepository = Depends(get_pricing_repo),
):
    shift = await shift_service.get_active_shift(current_user.id)
    sessions, total = [], 0
    if shift:
        if current_user.gate_number == 1:
            sessions, total = await session_repo.get_by_shift(shift.id, page=1, size=10)
        else:
            sessions, total = await session_repo.get_by_shift_combined(shift.id, page=1, size=10)

    active_rule = await pricing_repo.get_active()
    
    total_capacity = 50
    active_count = 5
    try:
        from models.parking_session import ParkingSession
        from sqlalchemy import func
        active_res = await session_repo.db.execute(
            select(func.count(ParkingSession.id)).where(ParkingSession.exit_time.is_(None), ParkingSession.is_deleted == False)
        )
        active_count = active_res.scalar() or 0
        from models.parking_card import ParkingCard
        card_count_res = await session_repo.db.execute(select(func.count(ParkingCard.id)))
        total_capacity = card_count_res.scalar() or 50
    except Exception:
        pass
    available_spaces = max(total_capacity - active_count, 0)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "operator/dashboard.html",
        {
            "request": request,
            "user": current_user,
            "shift": shift,
            "sessions": sessions,
            "session_total": total,
            "active_rule": active_rule,
            "total_capacity": total_capacity,
            "available_spaces": available_spaces,
            "active_sessions_count": active_count,
        },
    )


@router.get("/entry")
async def entry_scan_page(
    request: Request,
    current_user: User = Depends(require_operator),
):
    # Exit operators (gate_number == 2) cannot access entry
    if current_user.gate_number == 2:
        return RedirectResponse("/ui/operator/exit", status_code=303)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "operator/entry.html",
        {"request": request, "user": current_user, "error": None},
    )



@router.post("/entry")
async def process_entry(
    request: Request,
    card_code: str = Form(...),
    plate_number: str = Form(""),
    force_regular: int = Form(0),
    current_user: User = Depends(require_operator),
    session_service: SessionService = Depends(get_session_service),
    db: AsyncSession = Depends(get_db),
):
    # Exit operators cannot process entry
    if current_user.gate_number == 2:
        return RedirectResponse("/ui/operator/exit", status_code=303)

    templates = request.app.state.templates
    cleaned_card_code = card_code.strip()

    # If not forcing regular session, check if card belongs to an expired subscription
    if not force_regular:
        try:
            card_res = await db.execute(select(ParkingCard).where(ParkingCard.card_code == cleaned_card_code).limit(1))
            card_obj = card_res.scalars().first()
            if card_obj:
                from models.subscription import Subscription, SubscriptionStatus
                sub_res = await db.execute(
                    select(Subscription)
                    .where(Subscription.card_id == card_obj.id)
                    .order_by(Subscription.id.desc())
                    .limit(1)
                )
                latest_sub = sub_res.scalars().first()
                if latest_sub and latest_sub.status == SubscriptionStatus.EXPIRED:
                    sub_repo_local = SubscriberRepository(db)
                    plan_repo_local = SubscriptionPlanRepository(db)
                    subscriber_obj = await sub_repo_local.get_by_id(latest_sub.subscriber_id)
                    plan_obj = await plan_repo_local.get_by_id(latest_sub.plan_id)
                    return templates.TemplateResponse(
                        "operator/entry_expired_alert.html",
                        {
                            "request": request,
                            "user": current_user,
                            "subscription": latest_sub,
                            "subscriber": subscriber_obj,
                            "card": card_obj,
                            "plan": plan_obj,
                        },
                    )
        except Exception as check_err:
            import traceback
            traceback.print_exc()
    try:
        res = await session_service.open_session(
            card_code=card_code,
            operator_id=current_user.id,
            plate_number=plate_number or None,
        )
        session = res.session if hasattr(res, "session") else res[0] if isinstance(res, tuple) else res
    except SubscriptionDailyLimitReachedError as e:
        templates = request.app.state.templates
        return templates.TemplateResponse(
            "operator/entry.html",
            {"request": request, "user": current_user, "error": translations.get("errors.subscription_daily_limit", e.message)},
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        key = get_error_translation_key(e)
        err_msg = translations.get(key, getattr(e, "message", "Internal server error"))
        templates = request.app.state.templates
        return templates.TemplateResponse(
            "operator/entry.html",
            {"request": request, "user": current_user, "error": err_msg},
        )

    return RedirectResponse(f"/ui/operator/entry/confirm/{session.id}", status_code=303)


@router.get("/entry/confirm/{session_id}")
async def entry_confirm_page(
    request: Request,
    session_id: int,
    current_user: User = Depends(require_operator),
    db: AsyncSession = Depends(get_db),
    session_repo: ParkingSessionRepository = Depends(get_session_repo),
):
    session = await session_repo.get_by_id(session_id)
    if not session:
        return RedirectResponse("/ui/operator/dashboard", status_code=303)

    subscription = None
    subscriber = None
    plan = None
    if session.subscription_id:
        sub_repo = SubscriptionRepository(db)
        subscriber_repo = SubscriberRepository(db)
        plan_repo = SubscriptionPlanRepository(db)
        subscription = await sub_repo.get_by_id(session.subscription_id)
        if subscription:
            subscriber = await subscriber_repo.get_by_id(subscription.subscriber_id)
            plan = await plan_repo.get_by_id(subscription.plan_id)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "operator/entry_confirm.html",
        {
            "request": request,
            "user": current_user,
            "session": session,
            "subscription": subscription,
            "subscriber": subscriber,
            "plan": plan,
        },
    )


@router.get("/exit")
async def exit_scan_page(
    request: Request,
    error: str | None = None,
    current_user: User = Depends(require_operator),
):
    # Entry operators (gate_number == 1) cannot access exit
    if current_user.gate_number == 1:
        return RedirectResponse("/ui/operator/entry", status_code=303)

    err_msg = None
    if error:
        err_msg = translations.get(f"errors.{error.lower()}", error)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "operator/exit_scan.html",
        {"request": request, "user": current_user, "error": err_msg},
    )


@router.post("/exit/lookup")
async def exit_lookup(
    request: Request,
    card_code: str = Form(...),
    current_user: User = Depends(require_operator),
    db: AsyncSession = Depends(get_db),
    session_repo: ParkingSessionRepository = Depends(get_session_repo),
    pricing_service: PricingService = Depends(get_pricing_service),
):
    # Entry operators cannot process exit
    if current_user.gate_number == 1:
        return RedirectResponse("/ui/operator/entry", status_code=303)

    card_service = CardService(db)
    sub_repo = SubscriptionRepository(db)
    try:
        session = await card_service.validate_for_exit(card_code, session_repo)
        subscription = None
        if getattr(session, "is_subscribed", False) and session.subscription_id:
            subscription = await sub_repo.get_by_id(session.subscription_id)
            calc = None
            rule = None
        else:
            rule = await pricing_service.get_active_rule()
            calc = pricing_service.calculate(session, rule, datetime.utcnow())
    except Exception as e:
        import traceback
        traceback.print_exc()
        key = get_error_translation_key(e)
        err_msg = translations.get(key, getattr(e, "message", "Internal server error"))
        templates = request.app.state.templates
        return templates.TemplateResponse(
            "operator/exit_scan.html",
            {"request": request, "user": current_user, "error": err_msg},
        )

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "operator/exit_confirm.html",
        {
            "request": request,
            "session": session,
            "calc": calc,
            "rule": rule,
            "is_subscribed": getattr(session, "is_subscribed", False),
            "subscription": subscription,
        },
    )


@router.post("/exit/{session_id}/confirm")
async def exit_confirm_session(
    request: Request,
    session_id: int,
    current_user: User = Depends(require_operator),
    session_service: SessionService = Depends(get_session_service),
):
    if current_user.gate_number == 1:
        return RedirectResponse("/ui/operator/entry", status_code=303)

    try:
        res = await session_service.close_session(session_id, current_user.id)
        session = res.session if hasattr(res, "session") else res[0] if isinstance(res, tuple) else res
        hub = getattr(request.app.state, "edge_hub", None)
        settings = getattr(request.app.state, "settings", None) or get_settings()
        if hub and settings.EDGE_API_TOKEN:
            asyncio.create_task(
                PlateCaptureService(None, settings).start_exit_capture(
                    session, hub, gate_id=getattr(current_user, "gate_number", 2) or 2
                )
            )
    except SessionNotActiveError:
        return RedirectResponse("/ui/operator/exit?error=SESSION_NOT_ACTIVE", status_code=303)
    except NoPricingRuleError as e:
        err_msg = translations.get("errors.no_pricing_rule", e.message)
        templates = request.app.state.templates
        return templates.TemplateResponse(
            "operator/exit_scan.html",
            {"request": request, "user": current_user, "error": err_msg},
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        templates = request.app.state.templates
        return templates.TemplateResponse(
            "operator/exit_scan.html",
            {"request": request, "user": current_user, "error": getattr(e, "message", str(e))},
        )

    return RedirectResponse(f"/ui/operator/receipt/{session.id}", status_code=303)


@router.get("/lost-card")
async def lost_card_page(
    request: Request,
    current_user: User = Depends(require_operator),
):
    if current_user.gate_number == 1:
        return RedirectResponse("/ui/operator/entry", status_code=303)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "operator/lost_card.html",
        {"request": request, "user": current_user, "error": None, "sessions": None},
    )


@router.post("/lost-card")
async def process_lost_card_lookup(
    request: Request,
    plate_number: str = Form(...),
    notes: str = Form(""),
    current_user: User = Depends(require_operator),
    session_service: SessionService = Depends(get_session_service),
):
    if current_user.gate_number == 1:
        return RedirectResponse("/ui/operator/entry", status_code=303)

    sessions = await session_service.find_active_by_plate(plate_number)
    templates = request.app.state.templates
    if not sessions:
        err_msg = translations.get("errors.no_active_session_for_plate", "No active sessions found for this plate")
        return templates.TemplateResponse(
            "operator/lost_card.html",
            {"request": request, "user": current_user, "error": err_msg, "sessions": None},
        )
    elif len(sessions) == 1:
        return RedirectResponse(f"/ui/operator/lost-card/confirm/{sessions[0].id}", status_code=303)

    return templates.TemplateResponse(
        "operator/lost_card.html",
        {"request": request, "user": current_user, "error": None, "sessions": sessions, "notes": notes},
    )


@router.get("/lost-card/confirm/{session_id}")
async def lost_card_confirm_page(
    request: Request,
    session_id: int,
    notes: str | None = None,
    current_user: User = Depends(require_operator),
    session_repo: ParkingSessionRepository = Depends(get_session_repo),
    pricing_service: PricingService = Depends(get_pricing_service),
):
    if current_user.gate_number == 1:
        return RedirectResponse("/ui/operator/entry", status_code=303)

    session = await session_repo.get_by_id(session_id)
    if not session:
        return RedirectResponse("/ui/operator/lost-card", status_code=303)

    rule = await pricing_service.get_active_rule()
    calc = pricing_service.calculate_lost_card(session, rule, datetime.utcnow())

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "operator/lost_card_confirm.html",
        {"request": request, "session": session, "calc": calc, "rule": rule, "notes": notes},
    )


@router.post("/lost-card/confirm/{session_id}")
async def resolve_lost_card_confirm(
    session_id: int,
    notes: str = Form(""),
    current_user: User = Depends(require_operator),
    session_service: SessionService = Depends(get_session_service),
):
    if current_user.gate_number == 1:
        return RedirectResponse("/ui/operator/entry", status_code=303)

    await session_service.resolve_lost_card(
        session_id=session_id,
        operator_id=current_user.id,
        notes=notes or None,
    )
    return RedirectResponse(f"/ui/operator/receipt/{session_id}", status_code=303)


@router.get("/receipt/{session_id}")
async def print_receipt_page(
    request: Request,
    session_id: int,
    current_user: User = Depends(require_operator),
    db: AsyncSession = Depends(get_db),
    session_repo: ParkingSessionRepository = Depends(get_session_repo),
    session_service: SessionService = Depends(get_session_service),
):
    session = await session_repo.get_by_id(session_id)
    if not session:
        return RedirectResponse("/ui/operator/dashboard", status_code=303)

    operator_name = "Unknown"
    if session.exit_operator_id is not None:
        operator_res = await db.execute(
            select(User.username).where(User.id == session.exit_operator_id).limit(1)
        )
        operator_name = operator_res.scalar() or "Unknown"

    await session_service.mark_receipt_printed(session_id)
    settings = get_settings()
    templates = request.app.state.templates

    # Handle Subscribed Session Receipt
    if getattr(session, "is_subscribed", False):
        sub_repo = SubscriptionRepository(db)
        subscriber_repo = SubscriberRepository(db)
        plan_repo = SubscriptionPlanRepository(db)

        subscription = None
        subscriber = None
        plan = None
        if session.subscription_id:
            subscription = await sub_repo.get_by_id(session.subscription_id)
            if subscription:
                subscriber = await subscriber_repo.get_by_id(subscription.subscriber_id)
                plan = await plan_repo.get_by_id(subscription.plan_id)

        receipt_data = ReceiptData(
            session_id=session.id,
            card_code=session.card_code,
            plate_number=session.plate_number or (subscriber.plate_number if subscriber else "غير مسجل"),
            gate_number=session.gate_number,
            operator_name=operator_name,
            entry_time=session.entry_time,
            exit_time=session.exit_time or datetime.utcnow(),
            duration_minutes=session.duration_minutes or 0,
            duration_display=format_duration(session.duration_minutes or 0),
            pricing_rule_label=plan.label if plan else "اشتراك",
            subscription_end_date=subscription.end_date if subscription else None,
            subscriber_name=subscriber.full_name if subscriber else "مشترك غير مسجل",
            rate_per_hour=0,
            grace_period_mins=0,
            base_amount=0,
            penalty_amount=0,
            total_amount=0,
            total_display="٠ ج.م",
            payment_method="اشتراك",
            is_lost_card=False,
            is_grace_period=False,
            company_name="garage4u",
            garage_name="جراج باب اللوق",
        )

        return templates.TemplateResponse(
            "receipts/thermal_subscription.html",
            {
                "request": request,
                "receipt": receipt_data,
                "garage_name": "جراج باب اللوق",
            },
        )

    # Regular Session Receipt
    rule_label = "Standard"
    rate_per_hour = 0
    grace_period_mins = 0
    lost_card_penalty = 0
    if session.pricing_rule_id is not None:
        rule_res = await db.execute(
            select(PricingRule).where(PricingRule.id == session.pricing_rule_id).limit(1)
        )
        rule = rule_res.scalars().first()
        if rule:
            rule_label = rule.label
            rate_per_hour = rule.rate_per_hour
            grace_period_mins = rule.grace_period_mins
            lost_card_penalty = rule.lost_card_penalty

    if session.is_lost_card:
        penalty_amount = lost_card_penalty
        base_amount = (session.amount_charged or 0) - lost_card_penalty
        is_grace_period = False
    else:
        penalty_amount = 0
        base_amount = session.amount_charged or 0
        is_grace_period = (session.duration_minutes or 0) <= grace_period_mins

    receipt_data = ReceiptData(
        session_id=session.id,
        card_code=session.card_code,
        plate_number=session.plate_number,
        gate_number=session.gate_number,
        operator_name=operator_name,
        entry_time=session.entry_time,
        exit_time=session.exit_time or datetime.utcnow(),
        duration_minutes=session.duration_minutes or 0,
        duration_display=format_duration(session.duration_minutes or 0),
        pricing_rule_label=rule_label,
        rate_per_hour=rate_per_hour,
        grace_period_mins=grace_period_mins,
        base_amount=base_amount,
        penalty_amount=penalty_amount,
        total_amount=session.amount_charged or 0,
        total_display=format_egp(session.amount_charged or 0),
        payment_method="نقدي",
        is_lost_card=session.is_lost_card,
        is_grace_period=is_grace_period,
        company_name="garage4u",
        garage_name="جراج باب اللوق",
    )

    return templates.TemplateResponse(
        "receipts/thermal.html",
        {
            "request": request,
            "receipt": receipt_data,
            "garage_name": settings.APP_NAME,
        },
    )


# ===========================================================================
# Unified Dashboard Fast POS APIs (Scan & One-Click Customer Exit Display)
# ===========================================================================


async def _start_plate_capture(request: Request, db: AsyncSession, settings, session: ParkingSession) -> dict | None:
    """Ask the garage edge PC for a plate snapshot. The entry is already saved;
    any failure here only degrades to "no plate", it never blocks the gate."""
    hub = getattr(request.app.state, "edge_hub", None)
    if hub is None or not settings.EDGE_API_TOKEN:
        return None
    try:
        capture_status = await PlateCaptureService(db, settings).start(session, hub)
    except Exception:
        logger.exception("Plate capture could not start for session %s", session.id)
        return None
    return {
        "status": capture_status.value,
        "poll_url": f"/ui/operator/api/sessions/{session.id}/plate",
        "timeout_ms": int(settings.PLATE_CAPTURE_TIMEOUT_S * 1000),
    }


def _can_view_session(user: User, session: ParkingSession) -> bool:
    return user.role == UserRole.ADMIN or session.operator_id == user.id


@router.post("/api/scan")
async def api_operator_scan(
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncSession = Depends(get_db),
    session_service: SessionService = Depends(get_session_service),
    session_repo: ParkingSessionRepository = Depends(get_session_repo),
    pricing_service: PricingService = Depends(get_pricing_service),
    shift_service: ShiftService = Depends(get_shift_service),
    settings=Depends(get_settings),
):
    shift = await shift_service.get_active_shift(current_user.id)
    if not shift:
        return JSONResponse(
            {"success": False, "error": "لا يوجد شيفت مفتوح حالياً. يرجى بدء الشيفت أولاً للتمكن من تسجيل العمليات."},
            status_code=400,
        )

    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            body = await request.json()
        else:
            form = await request.form()
            body = dict(form)
    except Exception:
        body = {}

    card_code = str(body.get("card_code") or "").strip()
    if not card_code:
        return JSONResponse({"success": False, "error": "يرجى مسح أو إدخال كود الكارت."}, status_code=400)

    # ----------------------------------------------------
    # GATE 1: ENTRY OPERATOR
    # ----------------------------------------------------
    if current_user.gate_number == 1:
        try:
            # Check expired subscription warning
            card_res = await db.execute(select(ParkingCard).where(ParkingCard.card_code == card_code).limit(1))
            card_obj = card_res.scalars().first()
            if card_obj:
                from models.subscription import Subscription, SubscriptionStatus
                sub_res = await db.execute(
                    select(Subscription)
                    .where(Subscription.card_id == card_obj.id)
                    .order_by(Subscription.id.desc())
                    .limit(1)
                )
                latest_sub = sub_res.scalars().first()
                if latest_sub and latest_sub.status == SubscriptionStatus.EXPIRED and not body.get("force_regular"):
                    sub_repo_local = SubscriberRepository(db)
                    plan_repo_local = SubscriptionPlanRepository(db)
                    subscriber_obj = await sub_repo_local.get_by_id(latest_sub.subscriber_id)
                    plan_obj = await plan_repo_local.get_by_id(latest_sub.plan_id)
                    return JSONResponse({
                        "success": False,
                        "expired_subscription": True,
                        "error": "تنبيه: هذا الاشتراك منتهي الصلاحية! يمكنك المتابعة كجلسة عادية.",
                        "subscriber_name": subscriber_obj.full_name if subscriber_obj else "غير محدد",
                        "plan_label": plan_obj.label if plan_obj else "اشتراك",
                    })

            res = await session_service.open_session(
                card_code=card_code,
                operator_id=current_user.id,
                plate_number=body.get("plate_number") or None,
            )
            session = res.session if hasattr(res, "session") else res[0] if isinstance(res, tuple) else res

            sub_info = None
            if session.subscription_id:
                sub_repo = SubscriptionRepository(db)
                sub_obj = await sub_repo.get_by_id(session.subscription_id)
                if sub_obj:
                    subscriber_repo = SubscriberRepository(db)
                    plan_repo = SubscriptionPlanRepository(db)
                    subscriber = await subscriber_repo.get_by_id(sub_obj.subscriber_id)
                    plan = await plan_repo.get_by_id(sub_obj.plan_id)
                    sub_info = {
                        "subscriber_name": subscriber.full_name if subscriber else "مشترك",
                        "plate_number": sub_obj.plate_number or (subscriber.plate_number if subscriber else ""),
                        "plan_label": plan.label if plan else "اشتراك معتمد",
                        "end_date": format_cairo_12h(sub_obj.end_date, "%Y/%m/%d") if sub_obj.end_date else "—",
                    }

            plate_capture = await _start_plate_capture(request, db, settings, session)

            return JSONResponse({
                "success": True,
                "type": "entry",
                "session_id": session.id,
                "card_code": session.card_code,
                "plate_number": session.plate_number or (sub_info["plate_number"] if sub_info else ""),
                "entry_time": format_cairo_12h(session.entry_time),
                "is_subscribed": bool(session.subscription_id),
                "subscription": sub_info,
                "plate_capture": plate_capture,
                "message": "تم تسجيل الدخول بنجاح",
            })
        except Exception as e:
            key = get_error_translation_key(e)
            err_msg = translations.get(key, getattr(e, "message", str(e)))
            return JSONResponse({"success": False, "error": err_msg}, status_code=400)

    # ----------------------------------------------------
    # GATE 2: EXIT OPERATOR (AUTOMATIC 1-STEP EXIT & CALCULATION)
    # ----------------------------------------------------
    else:
        card_service = CardService(db)
        sub_repo = SubscriptionRepository(db)
        try:
            # 1. Validate session
            session = await card_service.validate_for_exit(card_code, session_repo)
            
            # Extract current_user info before close_session commits and expires ORM attributes
            operator_id_val = int(current_user.id)
            gate_id_val = int(getattr(current_user, "gate_number", 2) or 2)

            # 2. Close session automatically
            res = await session_service.close_session(session.id, operator_id_val)
            closed_session = res[0] if isinstance(res, tuple) else (res.session if hasattr(res, "session") else res)
            calc = res[1] if isinstance(res, tuple) and len(res) > 1 else None

            # Extract scalar values immediately to avoid any detached instance issues
            closed_session_id = int(closed_session.id)
            card_code_val = str(closed_session.card_code)
            plate_number_val = str(closed_session.plate_number or "")
            is_sub = bool(getattr(closed_session, "is_subscribed", False))
            amount_piastres = int(closed_session.amount_charged or 0)
            entry_time_val = closed_session.entry_time
            duration_mins = int(closed_session.duration_minutes or 0)

            # Trigger background exit camera snapshot (no OCR, admin only)
            try:
                hub = getattr(request.app.state, "edge_hub", None)
                if hub and getattr(settings, "EDGE_API_TOKEN", None):
                    await PlateCaptureService(db, settings).start_exit_capture(
                        closed_session_id,
                        hub,
                        gate_id=gate_id_val,
                        card_code=card_code_val,
                    )
            except Exception as cam_exc:
                logger.error("Could not trigger exit camera snapshot: %s", cam_exc)

            total_egp = str(int(round(amount_piastres / 100)))
            try:
                total_formatted = format_egp(amount_piastres)
            except Exception:
                total_formatted = f"{total_egp} ج.م"
            
            rate_label = "عادي"
            rate_per_hour = ""
            if calc and hasattr(calc, "rate_per_hour"):
                try:
                    rate_per_hour = format_egp(calc.rate_per_hour)
                except Exception:
                    rate_per_hour = f"{int(round(getattr(calc, 'rate_per_hour', 0) / 100))} ج.م"
            else:
                try:
                    rule = await pricing_service.get_active_rule()
                    if rule:
                        rate_label = rule.label
                        rate_per_hour = format_egp(rule.rate_per_hour)
                except Exception:
                    pass

            try:
                entry_time_str = format_cairo_12h(entry_time_val)
            except Exception:
                entry_time_str = str(entry_time_val or "—")

            try:
                duration_str = format_duration(duration_mins)
            except Exception:
                duration_str = f"{duration_mins} دقيقة"

            return JSONResponse({
                "success": True,
                "type": "exit_completed",
                "session_id": closed_session_id,
                "card_code": card_code_val,
                "plate_number": plate_number_val,
                "is_subscribed": is_sub,
                "total_egp": total_egp,
                "total_formatted": total_formatted,
                "entry_time": entry_time_str,
                "duration_display": duration_str,
                "rate_label": "اشتراك معتمد (مجاني)" if is_sub else rate_label,
                "rate_per_hour": "" if is_sub else rate_per_hour,
                "receipt_url": f"/ui/operator/receipt/{closed_session_id}",
                "message": "تم تأكيد خروج السيارة وحساب المبلغ بنجاح",
            })
        except Exception as e:
            logger.exception("api_operator_scan exit failed: %s", e)
            key = get_error_translation_key(e)
            err_msg = translations.get(key, getattr(e, "message", str(e)))
            return JSONResponse({"success": False, "error": err_msg}, status_code=400)


@router.post("/api/exit/confirm")
async def api_operator_exit_confirm(
    request: Request,
    current_user: User = Depends(require_operator),
    session_service: SessionService = Depends(get_session_service),
):
    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            body = await request.json()
        else:
            form = await request.form()
            body = dict(form)
        session_id = int(body.get("session_id"))
    except Exception:
        return JSONResponse({"success": False, "error": "معرف الجلسة غير صحيح."}, status_code=400)

    try:
        res = await session_service.close_session(session_id, current_user.id)
        session = res.session if hasattr(res, "session") else res[0] if isinstance(res, tuple) else res
        hub = getattr(request.app.state, "edge_hub", None)
        settings = getattr(request.app.state, "settings", None) or get_settings()
        if hub and settings.EDGE_API_TOKEN:
            asyncio.create_task(
                PlateCaptureService(None, settings).start_exit_capture(
                    session, hub, gate_id=getattr(current_user, "gate_number", 2) or 2
                )
            )
        return JSONResponse({
            "success": True,
            "session_id": session.id,
            "receipt_url": f"/ui/operator/receipt/{session.id}",
            "message": "تم تأكيد الخروج بنجاح",
        })
    except Exception as e:
        key = get_error_translation_key(e)
        err_msg = translations.get(key, getattr(e, "message", str(e)))
        return JSONResponse({"success": False, "error": err_msg}, status_code=400)


@router.get("/api/recent-sessions")
async def api_operator_recent_sessions(
    current_user: User = Depends(require_operator),
    shift_service: ShiftService = Depends(get_shift_service),
    session_repo: ParkingSessionRepository = Depends(get_session_repo),
):
    shift = await shift_service.get_active_shift(current_user.id)
    if not shift:
        return JSONResponse({"sessions": [], "total": 0})
    sessions, total = await session_repo.get_by_shift_combined(shift.id, page=1, size=10)
    rows = []
    for s in sessions:
        rows.append({
            "card_code": s.card_code,
            "plate_number": s.plate_number,
            "entry_time": format_cairo_12h(s.entry_time),
            "exit_time": format_cairo_12h(s.exit_time) if s.exit_time else None,
            "amount_formatted": format_egp(s.amount_charged) if s.amount_charged is not None else None,
            "status": "inside" if not s.exit_time else "exited",
        })
    return JSONResponse({"sessions": rows, "total": total})






@router.get("/api/sessions/{session_id}/plate")
async def api_plate_capture_status(
    request: Request,
    session_id: int,
    wait: int = 0,
    current_user: User = Depends(require_operator),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
):
    """Plate capture state. With ?wait=1 this long-polls (up to
    PLATE_POLL_MAX_WAIT_S) until the capture leaves PENDING."""
    hub = getattr(request.app.state, "edge_hub", None)
    service = PlateCaptureService(db, settings)
    loop = asyncio.get_running_loop()
    wait_until = loop.time() + (settings.PLATE_POLL_MAX_WAIT_S if wait and hub else 0.0)

    waiter = hub.result_waiter(session_id) if hub else nullcontext(asyncio.Event())
    async with waiter as result_event:
        while True:
            # Clear before reading so a result arriving mid-read still wakes us.
            result_event.clear()
            session = await service.get_fresh(session_id)
            if session is None or not _can_view_session(current_user, session):
                return JSONResponse({"success": False, "error": "الجلسة غير موجودة."}, status_code=404)
            if session.plate_capture_status != PlateCaptureStatus.PENDING.value:
                break
            # +0.25s lets the next loop observe the deadline and mark TIMEOUT.
            budget = min(wait_until - loop.time(), service.remaining_s(session) + 0.25)
            if budget <= 0:
                break
            try:
                await asyncio.wait_for(result_event.wait(), min(budget, _PLATE_RECHECK_INTERVAL_S))
            except asyncio.TimeoutError:
                pass

    image_url = f"/ui/operator/api/sessions/{session.id}/plate-image"
    return JSONResponse({"success": True, "session_id": session.id, **capture_payload(session, image_url)})


@router.get("/api/sessions/{session_id}/plate-image")
async def api_plate_image(
    session_id: int,
    current_user: User = Depends(require_operator),
    db: AsyncSession = Depends(get_db),
    session_repo: ParkingSessionRepository = Depends(get_session_repo),
    settings=Depends(get_settings),
):
    session = await session_repo.get_by_id(session_id)
    if session is None or not _can_view_session(current_user, session):
        raise HTTPException(status_code=404, detail="Not found")
    path = PlateCaptureService(db, settings).resolve_image(session.plate_image_path)
    if path is None:
        raise HTTPException(status_code=404, detail="Not found")
    media_type = "image/jpeg" if path.endswith((".jpg", ".jpeg")) else "image/webp"
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=86400"})


@router.post("/api/sessions/{session_id}/plate-override")
async def api_operator_plate_override(
    session_id: int,
    request: Request,
    current_user: User = Depends(require_operator),
    db: AsyncSession = Depends(get_db),
    session_repo: ParkingSessionRepository = Depends(get_session_repo),
):
    try:
        body = await request.json()
        plate_number = str(body.get("plate_number") or "").strip()
    except Exception:
        return JSONResponse({"success": False, "error": "بيانات غير صالحة."}, status_code=400)

    session = await session_repo.get_by_id(session_id)
    if not session or not _can_view_session(current_user, session):
        return JSONResponse({"success": False, "error": "الجلسة غير موجودة."}, status_code=404)

    session.plate_number = plate_number or None
    await db.commit()
    return JSONResponse({"success": True, "plate_number": session.plate_number, "message": "تم تحديث اللوحة بنجاح"})

