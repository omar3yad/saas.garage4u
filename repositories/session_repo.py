from datetime import datetime
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.parking_session import ParkingSession, SessionStatus

class ParkingSessionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_active_by_card_id(self, card_id: int) -> ParkingSession | None:
        """Queries for the active session of a specific card_id."""
        result = await self.db.execute(
            select(ParkingSession).where(
                ParkingSession.card_id == card_id,
                ParkingSession.status == SessionStatus.ACTIVE
            ).limit(1)
        )
        return result.scalars().first()

    async def get_by_id_for_update(self, session_id: int) -> ParkingSession | None:
        """Fetches a session with a pessimistic lock (FOR UPDATE) if not on SQLite."""
        query = select(ParkingSession).where(ParkingSession.id == session_id)
        
        # Check if the session engine dialect is SQLite
        bind = self.db.bind
        is_sqlite = False
        if bind is not None and getattr(bind, "dialect", None) is not None:
            is_sqlite = bind.dialect.name == "sqlite"
            
        if not is_sqlite:
            query = query.with_for_update()

        result = await self.db.execute(query)
        return result.scalars().first()

    async def create(
        self,
        card_id: int,
        card_code: str,
        gate_number: int,
        shift_id: int,
        operator_id: int,
        plate_number: str | None,
    ) -> ParkingSession:
        """Creates a new active session with current UTC entry time, flushing changes."""
        session = ParkingSession(
            card_id=card_id,
            card_code=card_code,
            gate_number=gate_number,
            shift_id=shift_id,
            operator_id=operator_id,
            plate_number=plate_number,
            status=SessionStatus.ACTIVE,
            entry_time=datetime.utcnow(),
            is_paid=False,
            is_lost_card=False,
        )
        self.db.add(session)
        await self.db.flush()
        return session

    async def get_active_by_plate(self, plate_normalized: str) -> list[ParkingSession]:
        """Queries active sessions that match a normalized plate number."""
        result = await self.db.execute(
            select(ParkingSession).where(
                ParkingSession.status == SessionStatus.ACTIVE,
                ParkingSession.plate_number == plate_normalized
            )
        )
        return list(result.scalars().all())

    async def get_by_shift(
        self, shift_id: int, page: int = 1, size: int = 10
    ) -> tuple[list[ParkingSession], int]:
        """Returns a paginated list of sessions for a shift (newest first) and total count.
        Includes sessions entered in this shift (shift_id) OR exited in this shift (exit_shift_id).
        """
        from sqlalchemy import or_, case

        condition = or_(
            ParkingSession.shift_id == shift_id,
            ParkingSession.exit_shift_id == shift_id,
        )
        count_query = select(func.count()).select_from(ParkingSession).where(condition)

        count_result = await self.db.execute(count_query)
        total_count = count_result.scalar_one()

        offset_val = (page - 1) * size
        query = (
            select(ParkingSession)
            .where(condition)
            .order_by(
                case(
                    (ParkingSession.exit_shift_id == shift_id, ParkingSession.exit_time),
                    else_=ParkingSession.entry_time,
                ).desc(),
                ParkingSession.id.desc(),
            )
            .offset(offset_val)
            .limit(size)
        )
        result = await self.db.execute(query)
        sessions = list(result.scalars().all())

        return sessions, total_count

    async def get_by_shift_combined(
        self, shift_id: int, page: int = 1, size: int = 10
    ) -> tuple[list[ParkingSession], int]:
        """Returns sessions relevant to this shift for the operator's view:
        - ACTIVE sessions that were ENTERED in this shift (shift_id).
        - COMPLETED / LOST_CARD sessions that were EXITED in this shift (exit_shift_id).

        This ensures an operator sees all sessions they are financially responsible for,
        even if the car was originally checked in by a different operator/shift.
        """
        from sqlalchemy import or_, and_, case

        combined_condition = or_(
            # Active sessions entered in this shift
            and_(
                ParkingSession.shift_id == shift_id,
                ParkingSession.status == SessionStatus.ACTIVE,
            ),
            # Completed/lost-card sessions exited in this shift
            and_(
                ParkingSession.exit_shift_id == shift_id,
                ParkingSession.status.in_([SessionStatus.COMPLETED, SessionStatus.LOST_CARD]),
            ),
        )

        count_query = (
            select(func.count())
            .select_from(ParkingSession)
            .where(combined_condition)
        )
        count_result = await self.db.execute(count_query)
        total_count = count_result.scalar_one()

        offset_val = (page - 1) * size
        query = (
            select(ParkingSession)
            .where(combined_condition)
            .order_by(
                case(
                    (ParkingSession.exit_shift_id == shift_id, ParkingSession.exit_time),
                    else_=ParkingSession.entry_time,
                ).desc(),
                ParkingSession.id.desc(),
            )
            .offset(offset_val)
            .limit(size)
        )
        result = await self.db.execute(query)
        sessions = list(result.scalars().all())

        return sessions, total_count

    async def get_by_subscription_ids(
        self, subscription_ids: list[int], page: int = 1, size: int = 10
    ) -> tuple[list[ParkingSession], int]:
        """Returns a paginated list of sessions for specific subscription IDs."""
        if not subscription_ids:
            return [], 0
        query = select(ParkingSession).where(
            ParkingSession.subscription_id.in_(subscription_ids)
        )
        count_query = select(func.count()).select_from(ParkingSession).where(
            ParkingSession.subscription_id.in_(subscription_ids)
        )
        count_result = await self.db.execute(count_query)
        total_count = count_result.scalar_one()

        offset_val = (page - 1) * size
        query = query.order_by(ParkingSession.entry_time.desc()).offset(offset_val).limit(size)
        result = await self.db.execute(query)
        return list(result.scalars().all()), total_count

    async def get_by_id(self, session_id: int) -> ParkingSession | None:
        """Fetches a session by id without locking."""
        result = await self.db.execute(
            select(ParkingSession).where(ParkingSession.id == session_id).limit(1)
        )
        return result.scalars().first()

    async def count_by_shift_and_status(
        self, shift_id: int, status: SessionStatus | None = None
    ) -> int:
        """Returns the number of sessions in a shift, optionally filtered by status."""
        query = select(func.count()).select_from(ParkingSession).where(ParkingSession.shift_id == shift_id)
        if status is not None:
            query = query.where(ParkingSession.status == status)
        result = await self.db.execute(query)
        return result.scalar_one()

__all__ = ["ParkingSessionRepository"]
