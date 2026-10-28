import asyncio
import math
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.parking_session import ParkingSession, SessionStatus
from models.parking_card import ParkingCard, CardStatus
from repositories.session_repo import ParkingSessionRepository
from services.audit_service import AuditService
from services.card_service import CardService
from services.plate_service import PlateService
from services.pricing_service import PricingService, PriceCalculation
from services.shift_service import ShiftService
from services.exceptions import (
    CardAlreadyActiveError,
    CardNotAvailableError,
    SessionNotFoundError,
    SessionNotActiveError,
    SubscriptionDailyLimitReachedError,
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
    ):
        self.db = db
        self.card_service = card_service
        self.session_repo = session_repo
        self.pricing_service = pricing_service
        self.shift_service = shift_service
        self.audit_service = audit_service
        self.plate_service = plate_service
        self.subscription_service = subscription_service

    async def open_session(
        self,
        card_code: str,
        operator_id: int,
        plate_number: str | None = None,
    ) -> SessionOpenResult:
        """Atomically opens a new parking session, checking for active subscriptions."""
        shift = await self.shift_service.require_active_shift(operator_id)
        
        # Normalize and fetch card
        card = await self.card_service.get_by_code(card_code)
        if card.status in (CardStatus.LOST, CardStatus.DAMAGED):
            raise CardNotAvailableError(f"Card '{card_code}' is {card.status.value}")

        # Check if card currently has an active open session
        existing_session = await self.session_repo.get_active_by_card_id(card.id)
        if existing_session is not None:
            raise CardAlreadyActiveError(f"Card '{card_code}' is already active")

        if plate_number is not None:
            normalized_plate = self.plate_service.normalize(plate_number)
            if normalized_plate == "":
                plate_number = None
            else:
                plate_number = normalized_plate

        # Check for active subscription
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

        try:
            session = await self.session_repo.create(
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
            before=None,
            after={
                "card_code": card.card_code,
                "gate_number": shift.gate_number,
                "is_subscribed": session.is_subscribed,
            },
        )
        return SessionOpenResult(session, subscription)

    async def close_session(
        self,
        session_id: int,
        exit_operator_id: int,
    ) -> tuple[ParkingSession, PriceCalculation | None]:
        """Atomically closes an active parking session, handling subscriptions and pricing rules."""
        async with self._lock:
            await self.db.rollback()  # Reset transaction to see fresh commits
            exit_shift = await self.shift_service.require_active_shift(exit_operator_id)
            session = await self.session_repo.get_by_id_for_update(session_id)
            if not session:
                raise SessionNotFoundError("Session not found")

            if session.status != SessionStatus.ACTIVE:
                raise SessionNotActiveError("Session is not active")

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
                    before={"id": session.id, "status": "ACTIVE"},
                    after={
                        "status": "COMPLETED",
                        "exit_time": exit_time.isoformat(),
                        "amount_charged": 0,
                        "is_subscribed": True,
                    },
                )
                return session, None

            # Standard hourly session exit with pricing calculation
            rule = await self.pricing_service.get_active_rule()
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

    async def resolve_lost_card(
        self,
        session_id: int,
        operator_id: int,
        notes: str | None = None,
    ) -> tuple[ParkingSession, PriceCalculation]:
        """Resolves a session in which the user lost their card, applying penalty fees."""
        async with self._lock:
            await self.db.rollback()  # Reset transaction to see fresh commits
            shift = await self.shift_service.require_active_shift(operator_id)
            session = await self.session_repo.get_by_id_for_update(session_id)
            if not session:
                raise SessionNotFoundError("Session not found")

            if session.status != SessionStatus.ACTIVE:
                raise SessionNotActiveError("Session is not active")

            rule = await self.pricing_service.get_active_rule()
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

    async def find_active_by_plate(self, plate: str) -> list[ParkingSession]:
        """Finds active sessions by normalized plate string."""
        normalized = self.plate_service.normalize(plate)
        return await self.session_repo.get_active_by_plate(normalized)

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
            await self.db.rollback()
            session = await self.session_repo.get_by_id_for_update(session_id)
            if not session:
                raise SessionNotFoundError("Session not found")

            if session.status != SessionStatus.ACTIVE:
                raise SessionNotActiveError("Session is not active")

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
            if not session.is_subscribed:
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

