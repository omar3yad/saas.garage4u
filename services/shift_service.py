import logging
from datetime import datetime
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.shift import Shift
from models.parking_session import ParkingSession, SessionStatus
from services.audit_service import AuditService
from services.exceptions import (
    ShiftAlreadyOpenError,
    NoActiveShiftError,
    ShiftNotFoundError,
    ShiftNotOwnedError,
    ShiftAlreadyClosedError,
)
from services.shift_summary import ShiftSummary

logger = logging.getLogger(__name__)

class ShiftService:
    def __init__(self, db: AsyncSession, audit_service: AuditService):
        self.db = db
        self.audit_service = audit_service

    async def get_active_shift(self, operator_id: int) -> Shift | None:
        """Queries for an active (not ended) shift for the operator."""
        result = await self.db.execute(
            select(Shift).where(
                Shift.operator_id == operator_id,
                Shift.ended_at.is_(None)
            ).limit(1)
        )
        return result.scalars().first()

    async def require_active_shift(self, operator_id: int) -> Shift:
        """Guard method that returns the active shift or raises NoActiveShiftError."""
        shift = await self.get_active_shift(operator_id)
        if not shift:
            raise NoActiveShiftError("No active shift found for this operator")
        return shift

    async def open_shift(
        self, operator_id: int, gate_number: int, opening_cash_egp: int
    ) -> Shift:
        """Opens a new shift for the operator if none is currently active."""
        active = await self.get_active_shift(operator_id)
        if active:
            raise ShiftAlreadyOpenError("Operator already has an active shift")

        shift = Shift(
            operator_id=operator_id,
            gate_number=gate_number,
            opening_cash_egp=opening_cash_egp,
            started_at=datetime.utcnow(),
        )
        self.db.add(shift)
        await self.db.flush()
        await self.db.commit()
        await self.db.refresh(shift)

        await self.audit_service.log(
            actor_id=operator_id,
            action="SHIFT_OPENED",
            entity_type="shift",
            entity_id=shift.id,
            after={
                "operator_id": operator_id,
                "gate_number": gate_number,
                "opening_cash_egp": opening_cash_egp,
            }
        )
        return shift

    async def close_shift(
        self,
        shift_id: int,
        operator_id: int,
        closing_cash_piastres: int,
    ) -> ShiftSummary:
        """Closes an active shift, computes financial summary, and logs the action."""
        result = await self.db.execute(
            select(Shift).where(Shift.id == shift_id).limit(1)
        )
        shift = result.scalars().first()
        if not shift:
            raise ShiftNotFoundError("Shift not found")

        if shift.operator_id != operator_id:
            raise ShiftNotOwnedError("Shift belongs to a different operator")

        shift.ended_at = datetime.utcnow()
        shift.closing_cash_egp = closing_cash_piastres

        summary = await self._compute_summary(shift, closing_cash_piastres)
        await self.db.flush()
        await self.db.commit()
        await self.db.refresh(shift)

        await self.audit_service.log(
            actor_id=operator_id,
            action="SHIFT_CLOSED",
            entity_type="shift",
            entity_id=shift.id,
            before={
                "id": shift.id,
                "operator_id": shift.operator_id,
                "gate_number": shift.gate_number,
                "started_at": shift.started_at.isoformat() if shift.started_at else None,
            },
            after={
                "ended_at": shift.ended_at.isoformat() if shift.ended_at else None,
                "closing_cash_egp": closing_cash_piastres,
            }
        )
        return summary

    async def _compute_summary(
        self, shift: Shift, closing_cash_piastres: int | None
    ) -> ShiftSummary:
        """Helper that computes the financial and session statistics for the shift.

        Session counts (entered/active) are based on shift_id (entry shift).
        Revenue is based on exit_shift_id (exit shift) — the operator who
        checks out the car is credited for the revenue, not the one who
        checked it in.
        """
        # Sessions that were ENTERED in this shift and still ACTIVE
        entry_result = await self.db.execute(
            select(ParkingSession).where(
                ParkingSession.shift_id == shift.id,
                ParkingSession.status == SessionStatus.ACTIVE,
            )
        )
        active_sessions_list = entry_result.scalars().all()
        active_sessions = len(active_sessions_list)

        # Sessions that were EXITED in this shift (revenue sessions)
        from sqlalchemy import or_, and_
        exit_result = await self.db.execute(
            select(ParkingSession).where(
                or_(
                    ParkingSession.exit_shift_id == shift.id,
                    and_(
                        ParkingSession.exit_shift_id.is_(None),
                        ParkingSession.shift_id == shift.id,
                    ),
                ),
                ParkingSession.status.in_(
                    [SessionStatus.COMPLETED, SessionStatus.LOST_CARD]
                ),
            )
        )
        exited_sessions = exit_result.scalars().all()

        completed_sessions = sum(1 for s in exited_sessions if s.status == SessionStatus.COMPLETED)
        lost_card_sessions = sum(1 for s in exited_sessions if s.status == SessionStatus.LOST_CARD)

        # total = sessions exited in this shift + still-active sessions entered in this shift
        total_sessions = len(exited_sessions) + active_sessions

        computed_total = sum(
            s.amount_charged for s in exited_sessions
            if s.amount_charged is not None
        )

        discrepancy = None
        if closing_cash_piastres is not None:
            discrepancy = closing_cash_piastres - computed_total

        return ShiftSummary(
            shift_id=shift.id,
            operator_id=shift.operator_id,
            gate_number=shift.gate_number,
            started_at=shift.started_at,
            ended_at=shift.ended_at,
            total_sessions=total_sessions,
            completed_sessions=completed_sessions,
            lost_card_sessions=lost_card_sessions,
            active_sessions=active_sessions,
            computed_total_piastres=computed_total,
            closing_cash_piastres=closing_cash_piastres,
            discrepancy_piastres=discrepancy,
        )

    async def force_close_shift(
        self,
        shift_id: int,
        admin_id: int,
        closing_cash_piastres: int | None,
        admin_note: str | None,
    ) -> ShiftSummary:
        """Force-closes a shift by an admin, updates closing cash & notes, and logs audit."""
        result = await self.db.execute(
            select(Shift).where(Shift.id == shift_id).limit(1)
        )
        shift = result.scalars().first()
        if not shift:
            raise ShiftNotFoundError("Shift not found")

        if shift.ended_at is not None:
            raise ShiftAlreadyClosedError("Shift is already closed")

        before_state = {
            "ended_at": None,
            "closing_cash_egp": shift.closing_cash_egp,
        }

        shift.ended_at = datetime.utcnow()
        if closing_cash_piastres is not None:
            shift.closing_cash_egp = closing_cash_piastres
        if admin_note is not None:
            shift.admin_override_note = admin_note

        await self.db.flush()
        summary = await self._compute_summary(shift, shift.closing_cash_egp)
        await self.db.commit()
        await self.db.refresh(shift)

        try:
            await self.audit_service.log(
                actor_id=admin_id,
                action="SHIFT_FORCE_CLOSED",
                entity_type="shift",
                entity_id=shift_id,
                before=before_state,
                after={
                    "ended_at": shift.ended_at.isoformat() if shift.ended_at else None,
                    "closing_cash_egp": shift.closing_cash_egp,
                    "admin_note": admin_note,
                },
            )
            await self.db.commit()
        except Exception as e:
            logger.error(f"Failed to log audit for force_close_shift {shift_id}: {e}")

        return summary


__all__ = ["ShiftService"]
