import asyncio
import math
import secrets
from datetime import datetime
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.parking_session import ParkingSession, SessionStatus
from models.parking_card import ParkingCard, CardStatus
from models.owner import Owner
from models.garage import Garage
from repositories.session_repo import ParkingSessionRepository
from repositories.garage_repo import GarageRepository
from repositories.garage_config_repo import GarageConfigRepository
from services.audit_service import AuditService
from services.card_service import CardService
from services.plate_service import PlateService
from services.pricing_service import PricingService, PriceCalculation
from services.shift_service import ShiftService
from services.counter_service import CounterService
from services.exceptions import (
    CardAlreadyActiveError,
    CardNotAvailableError,
    CardNotFoundError,
    SessionNotFoundError,
    SessionNotActiveError,
    SubscriptionDailyLimitReachedError,
    TenantNotFoundError,
    TenantSuspendedError,
    BillingPastDueError,
    TenantTerminatedError,
    ShortSessionConfirmationRequiredError,
    RapidDoubleScanError,
)


class SessionOpenResult(tuple):
    """2-tuple (session, subscription) that allows transparent attribute access to session."""
    def __new__(cls, session, subscription=None):
        return super().__new__(cls, (session, subscription))

    @property
    def session(self) -> ParkingSession:
        return self[0]

    @property
    def subscription(self):
        return self[1]

    def __getattr__(self, name: str):
        return getattr(self[0], name)


class SessionService:
    _lock = asyncio.Lock()

    def __init__(
        self,
        db: AsyncSession,
        card_service: CardService,
        session_repo: ParkingSessionRepository,
        pricing_service: PricingService,
        shift_service: ShiftService,
        audit_service: AuditService,
        plate_service: PlateService,
        subscription_service=None,
        counter_service: CounterService | None = None,
        config_repo: GarageConfigRepository | None = None,
        garage_repo: GarageRepository | None = None,
    ):
        self.db = db
        self.card_service = card_service
        self.session_repo = session_repo
        self.pricing_service = pricing_service
        self.shift_service = shift_service
        self.audit_service = audit_service
        self.plate_service = plate_service
        self.subscription_service = subscription_service
        self.counter_service = counter_service or CounterService(db)
        self.config_repo = config_repo or GarageConfigRepository(db)
        self.garage_repo = garage_repo or GarageRepository(db)

    async def _check_entry_allowed(self, garage_id: int) -> None:
        """Enforces tenant status and billing status for entry operations.
        Suspended or Past Due tenants are blocked from new entries (Exit-Only grace mode).
        """
        garage = await self.garage_repo.get_by_id(garage_id)
        if not garage:
            raise TenantNotFoundError(f"Garage {garage_id} not found")
        if garage.status == "TERMINATED":
            raise TenantTerminatedError("Garage operations have been terminated")
        if garage.status == "SUSPENDED":
            raise TenantSuspendedError("Garage is suspended (Exit-Only grace mode active)")

        result = await self.db.execute(select(Owner).where(Owner.id == garage.owner_id).limit(1))
        owner = result.scalars().first()
        if owner:
            if owner.billing_status == "PAST_DUE":
                raise BillingPastDueError("Owner subscription is past due (Exit-Only grace mode active)")
            if owner.billing_status in ("SUSPENDED", "TERMINATED"):
                raise TenantSuspendedError("Owner subscription is suspended")

    async def _check_exit_allowed(self, garage_id: int) -> None:
        """Enforces tenant status for exit operations.
        Terminated garages are blocked, but SUSPENDED or PAST_DUE tenants are permitted
        under Exit-Only Grace Mode to allow existing vehicles to depart.
        """
        garage = await self.garage_repo.get_by_id(garage_id)
        if not garage:
            raise TenantNotFoundError(f"Garage {garage_id} not found")
        if garage.status == "TERMINATED":
            raise TenantTerminatedError("Garage operations have been terminated")

    async def _acquire_advisory_lock(self, garage_id: int, card_id: int | None) -> None:
        """Acquires a PostgreSQL two-int transaction-level advisory lock:
        SELECT pg_try_advisory_xact_lock(:garage_id, :card_id)
        Throws RapidDoubleScanError (429) if lock acquisition fails.
        """
        if card_id is None:
            return
        try:
            bind = self.db.bind
            if bind is not None and getattr(bind, "dialect", None) is not None:
                if bind.dialect.name != "postgresql":
                    return

            res = await self.db.execute(
                text("SELECT pg_try_advisory_xact_lock(:gid, :cid)"),
                {"gid": int(garage_id), "cid": int(card_id)},
            )
            locked = res.scalar()
            if locked is False:
                raise RapidDoubleScanError(f"Rapid double scan detected for card {card_id}")
        except RapidDoubleScanError:
            raise
        except Exception:
            # Fallback if DB dialect or connection does not support advisory lock
            pass

    async def open_session(
        self,
        card_code: str,
        operator_id: int,
        plate_number: str | None = None,
        garage_id: int | None = None,
    ) -> SessionOpenResult:
        """Atomically opens a new parking session with a reusable card, checking subscriptions and tenant status."""
        shift = await self.shift_service.require_active_shift(operator_id)
        gid = garage_id or shift.garage_id

        # 1. Enforce tenant status (Exit-Only check)
        await self._check_entry_allowed(gid)

        # 2. Normalize and fetch card
        card = await self.card_service.get_by_code(card_code)
        if card.status in (CardStatus.LOST, CardStatus.DAMAGED):
            raise CardNotAvailableError(f"Card '{card_code}' is {card.status.value}")

        # 3. Prevent rapid double scan via two-int advisory lock
        await self._acquire_advisory_lock(gid, card.id)

        # 4. Check if card currently has an active open session
        existing_session = await self.session_repo.get_active_by_card_id(card.id, garage_id=gid)
        if existing_session is not None:
            raise CardAlreadyActiveError(f"Card '{card_code}' is already active")

        if plate_number is not None:
            normalized_plate = self.plate_service.normalize(plate_number)
            plate_number = normalized_plate if normalized_plate != "" else None

        # 5. Check for active subscription
        subscription = None
        if self.subscription_service is not None:
            subscription = await self.subscription_service.get_active_for_card(card.id)
            if subscription is not None:
                allowed = await self.subscription_service.check_daily_limit(subscription)
                if not allowed:
                    raise SubscriptionDailyLimitReachedError(
                        "Daily entry limit reached for this subscription"
                    )

        # For non-subscribed cards, card status must not be IN_USE without active session
        if subscription is None and card.status == CardStatus.IN_USE:
            raise CardAlreadyActiveError(f"Card '{card_code}' is already active")

        # 6. Atomic sequential receipt number
        receipt_no = await self.counter_service.get_next_receipt_number(gid)

        try:
            session = await self.session_repo.create(
                garage_id=gid,
                receipt_no=receipt_no,
                card_id=card.id,
                card_code=card.card_code,
                gate_number=shift.gate_number,
                shift_id=shift.id,
                operator_id=operator_id,
                plate_number=plate_number,
            )
            if subscription is not None:
                session.subscription_id = subscription.id
                session.is_subscribed = True

            await self.card_service.set_status(card, CardStatus.IN_USE)
            await self.db.commit()
            await self.db.refresh(session)
        except Exception as e:
            await self.db.rollback()
            from sqlalchemy.exc import IntegrityError
            if isinstance(e, IntegrityError) or "UNIQUE constraint failed" in str(e):
                raise CardAlreadyActiveError(f"Card '{card_code}' is already active")
            raise

        await self.audit_service.log(
            actor_id=operator_id,
            action="SESSION_OPENED",
            entity_type="parking_session",
            entity_id=session.id,
            garage_id=gid,
            before=None,
            after={
                "card_code": card.card_code,
                "receipt_no": receipt_no,
                "gate_number": shift.gate_number,
                "is_subscribed": session.is_subscribed,
            },
        )
        return SessionOpenResult(session, subscription)

    async def open_paper_ticket_session(
        self,
        operator_id: int,
        plate_number: str | None = None,
        garage_id: int | None = None,
    ) -> SessionOpenResult:
        """Opens a new session with a random non-sequential paper ticket barcode (e.g. TK-XXXXXX)."""
        shift = await self.shift_service.require_active_shift(operator_id)
        gid = garage_id or shift.garage_id

        # 1. Enforce tenant status
        await self._check_entry_allowed(gid)

        # 2. Generate random non-sequential ticket code
        ticket_code = f"TK-{secrets.token_hex(4).upper()}"

        # 3. Atomic sequential receipt number
        receipt_no = await self.counter_service.get_next_receipt_number(gid)

        if plate_number is not None:
            normalized_plate = self.plate_service.normalize(plate_number)
            plate_number = normalized_plate if normalized_plate != "" else None

        session = await self.session_repo.create(
            garage_id=gid,
            receipt_no=receipt_no,
            ticket_code=ticket_code,
            card_id=None,
            card_code=None,
            gate_number=shift.gate_number,
            shift_id=shift.id,
            operator_id=operator_id,
            plate_number=plate_number,
        )
        await self.db.commit()
        await self.db.refresh(session)

        await self.audit_service.log(
            actor_id=operator_id,
            action="SESSION_OPENED",
            entity_type="parking_session",
            entity_id=session.id,
            garage_id=gid,
            before=None,
            after={
                "ticket_code": ticket_code,
                "receipt_no": receipt_no,
                "gate_number": shift.gate_number,
                "is_paper_ticket": True,
            },
        )
        return SessionOpenResult(session, None)

    async def close_session(
        self,
        session_id: int,
        exit_operator_id: int,
        confirm_short_session: bool = False,
    ) -> tuple[ParkingSession, PriceCalculation | None]:
        """Atomically closes an active parking session, handling subscriptions, pricing rules,
        advisory locks, and short session confirmation checks.
        """
        async with self._lock:
            exit_shift = await self.shift_service.require_active_shift(exit_operator_id)
            session = await self.session_repo.get_by_id_for_update(session_id)
            if not session:
                raise SessionNotFoundError("Session not found")

            if session.status != SessionStatus.ACTIVE:
                raise SessionNotActiveError("Session is not active")

            # 1. Enforce exit grace check (SUSPENDED and PAST_DUE permitted, TERMINATED blocked)
            await self._check_exit_allowed(session.garage_id)

            # 2. Rapid double scan advisory lock
            await self._acquire_advisory_lock(session.garage_id, session.card_id)

            # 3. Minimum session duration check & confirmation guard
            elapsed_seconds = max(0, int((datetime.utcnow() - session.entry_time).total_seconds()))
            config = await self.config_repo.get_by_garage_id(session.garage_id)
            min_seconds = config.min_session_duration_seconds if config else 60

            if elapsed_seconds < min_seconds and not confirm_short_session:
                raise ShortSessionConfirmationRequiredError(
                    elapsed_seconds=elapsed_seconds,
                    min_seconds=min_seconds,
                )

            # Check if session is a subscribed session
            if session.is_subscribed:
                exit_time = datetime.utcnow()
                session.exit_time = exit_time
                session.status = SessionStatus.COMPLETED
                session.amount_charged = 0
                session.is_paid = True
                session.pricing_rule_id = None
                session.exit_operator_id = exit_operator_id
                session.exit_shift_id = exit_shift.id
                session.duration_minutes = math.ceil(
                    (exit_time - session.entry_time).total_seconds() / 60
                )

                # For subscribed cards, card remains assigned to subscriber (IN_USE)
                if session.card_id:
                    result = await self.db.execute(
                        select(ParkingCard).where(ParkingCard.id == session.card_id).limit(1)
                    )
                    card = result.scalars().first()
                    if card:
                        await self.card_service.set_status(card, CardStatus.IN_USE)

                await self.db.commit()
                await self.db.refresh(session)

                await self.audit_service.log(
                    actor_id=exit_operator_id,
                    action="SESSION_CLOSED",
                    entity_type="parking_session",
                    entity_id=session.id,
                    garage_id=session.garage_id,
                    before={"id": session.id, "status": "ACTIVE"},
                    after={
                        "status": "COMPLETED",
                        "exit_time": exit_time.isoformat(),
                        "amount_charged": 0,
                        "is_subscribed": True,
                    },
                )
                return session, None

            # Standard session exit with pricing calculation
            rule = await self.pricing_service.get_active_rule(garage_id=session.garage_id)
            exit_time = datetime.utcnow()
            calc = self.pricing_service.calculate(session, rule, exit_time)

            session.status = SessionStatus.COMPLETED
            session.exit_time = exit_time
            session.exit_operator_id = exit_operator_id
            session.exit_shift_id = exit_shift.id
            session.duration_minutes = calc.duration_minutes
            session.amount_charged = calc.total_amount
            session.pricing_rule_id = rule.id
            session.is_paid = True

            # If reusable card, return to AVAILABLE
            if session.card_id:
                result = await self.db.execute(
                    select(ParkingCard).where(ParkingCard.id == session.card_id).limit(1)
                )
                card = result.scalars().first()
                if card:
                    await self.card_service.set_status(card, CardStatus.AVAILABLE)

            await self.db.commit()
            await self.db.refresh(session)

            await self.audit_service.log(
                actor_id=exit_operator_id,
                action="SESSION_CLOSED",
                entity_type="parking_session",
                entity_id=session.id,
                garage_id=session.garage_id,
                before={
                    "id": session.id,
                    "status": "ACTIVE",
                },
                after={
                    "status": "COMPLETED",
                    "exit_time": exit_time.isoformat(),
                    "amount_charged": calc.total_amount,
                },
            )
            return session, calc

    async def close_session_by_code(
        self,
        code: str,
        exit_operator_id: int,
        confirm_short_session: bool = False,
        garage_id: int | None = None,
    ) -> tuple[ParkingSession, PriceCalculation | None]:
        """Resolves whether code is a paper ticket_code or a reusable card_code, and closes the session."""
        # 1. Search active paper ticket
        session = await self.session_repo.get_active_by_ticket_code(code, garage_id=garage_id)
        if not session:
            # 2. Search active card session
            try:
                card = await self.card_service.get_by_code(code)
                if card:
                    session = await self.session_repo.get_active_by_card_id(card.id, garage_id=garage_id)
            except CardNotFoundError:
                pass

        if not session:
            raise SessionNotFoundError(f"No active session found for code '{code}'")

        return await self.close_session(
            session_id=session.id,
            exit_operator_id=exit_operator_id,
            confirm_short_session=confirm_short_session,
        )

    async def resolve_lost_card(
        self,
        session_id: int,
        operator_id: int,
        notes: str | None = None,
    ) -> tuple[ParkingSession, PriceCalculation]:
        """Resolves a session in which the user lost their card or paper ticket, applying penalty fees."""
        async with self._lock:
            shift = await self.shift_service.require_active_shift(operator_id)
            session = await self.session_repo.get_by_id_for_update(session_id)
            if not session:
                raise SessionNotFoundError("Session not found")

            if session.status != SessionStatus.ACTIVE:
                raise SessionNotActiveError("Session is not active")

            # Check exit grace
            await self._check_exit_allowed(session.garage_id)

            rule = await self.pricing_service.get_active_rule(garage_id=session.garage_id)
            exit_time = datetime.utcnow()
            calc = self.pricing_service.calculate_lost_card(session, rule, exit_time)

            session.status = SessionStatus.LOST_CARD
            session.exit_time = exit_time
            session.is_lost_card = True
            session.exit_operator_id = operator_id
            session.exit_shift_id = shift.id
            session.duration_minutes = calc.duration_minutes
            session.amount_charged = calc.total_amount
            session.pricing_rule_id = rule.id
            session.lost_card_penalty_applied = rule.lost_card_penalty
            session.is_paid = True
            session.notes = notes

            if session.card_id:
                result = await self.db.execute(
                    select(ParkingCard).where(ParkingCard.id == session.card_id).limit(1)
                )
                card = result.scalars().first()
                if card:
                    await self.card_service.set_status(card, CardStatus.LOST)

            await self.db.commit()
            await self.db.refresh(session)

            await self.audit_service.log(
                actor_id=operator_id,
                action="LOST_CARD_RESOLVED",
                entity_type="parking_session",
                entity_id=session.id,
                garage_id=session.garage_id,
                before={
                    "id": session.id,
                    "status": "ACTIVE",
                },
                after={
                    "status": "LOST_CARD",
                    "exit_time": exit_time.isoformat(),
                    "amount_charged": calc.total_amount,
                    "is_lost_card": True,
                },
            )
            return session, calc

    async def find_active_by_plate(self, plate: str, garage_id: int | None = None) -> list[ParkingSession]:
        """Finds active sessions by normalized plate string."""
        normalized = self.plate_service.normalize(plate)
        return await self.session_repo.get_active_by_plate(normalized, garage_id=garage_id)

    async def mark_receipt_printed(self, session_id: int) -> None:
        """Idempotently records that the receipt for a session has been printed."""
        session = await self.session_repo.get_by_id(session_id)
        if not session:
            return

        if session.receipt_printed_at is None:
            session.receipt_printed_at = datetime.utcnow()
            await self.db.flush()
            await self.db.commit()
            await self.db.refresh(session)

    async def admin_override_close_session(
        self,
        session_id: int,
        admin_id: int,
        amount_charged_egp: float = 0.0,
        override_note: str | None = None,
    ) -> ParkingSession:
        """Allows an admin to close an active session without requiring an active shift,
        setting a custom/zero fee, recording override notes, freeing the card, and writing audit logs."""
        async with self._lock:
            session = await self.session_repo.get_by_id_for_update(session_id)
            if not session:
                raise SessionNotFoundError("Session not found")

            if session.status != SessionStatus.ACTIVE:
                raise SessionNotActiveError("Session is not active")

            await self._check_exit_allowed(session.garage_id)

            exit_time = datetime.utcnow()
            before_state = {
                "id": session.id,
                "status": session.status.value if hasattr(session.status, "value") else str(session.status),
                "amount_charged": session.amount_charged,
                "exit_time": str(session.exit_time),
            }

            amount_piastres = max(0, round(amount_charged_egp * 100))

            session.status = SessionStatus.COMPLETED
            session.exit_time = exit_time
            session.exit_operator_id = admin_id
            session.admin_override_by = admin_id
            session.admin_override_note = override_note or "إغلاق وتسوية إدارية"
            session.duration_minutes = math.ceil(
                (exit_time - session.entry_time).total_seconds() / 60
            )
            session.amount_charged = amount_piastres
            session.is_paid = True
            session.pricing_rule_id = None

            # Free card back to AVAILABLE if not a subscribed card
            if not session.is_subscribed and session.card_id:
                result = await self.db.execute(
                    select(ParkingCard).where(ParkingCard.id == session.card_id).limit(1)
                )
                card = result.scalars().first()
                if card:
                    await self.card_service.set_status(card, CardStatus.AVAILABLE)

            await self.db.commit()
            await self.db.refresh(session)

            await self.audit_service.log(
                actor_id=admin_id,
                action="ADMIN_SESSION_CLOSED",
                entity_type="parking_session",
                entity_id=session.id,
                garage_id=session.garage_id,
                before=before_state,
                after={
                    "status": "COMPLETED",
                    "exit_time": exit_time.isoformat(),
                    "amount_charged": session.amount_charged,
                    "admin_override_by": admin_id,
                    "admin_override_note": session.admin_override_note,
                },
            )
            return session


__all__ = ["SessionService", "SessionOpenResult"]
