from datetime import datetime
from sqlalchemy import select, func, or_, and_, case
from sqlalchemy.ext.asyncio import AsyncSession
from models.parking_session import ParkingSession, SessionStatus

class ParkingSessionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_active_by_card_id(self, card_id: int, garage_id: int | None = None) -> ParkingSession | None:
        """Queries for the active session of a specific card_id within a garage."""
        query = select(ParkingSession).where(
            ParkingSession.card_id == card_id,
            ParkingSession.status == SessionStatus.ACTIVE,
        )
        if garage_id is not None:
            query = query.where(ParkingSession.garage_id == garage_id)
        result = await self.db.execute(query.limit(1))
        return result.scalars().first()

    async def get_active_by_ticket_code(self, ticket_code: str, garage_id: int | None = None) -> ParkingSession | None:
        """Queries for the active session of a paper ticket within a garage."""
        query = select(ParkingSession).where(
            ParkingSession.ticket_code == ticket_code,
            ParkingSession.status == SessionStatus.ACTIVE,
        )
        if garage_id is not None:
            query = query.where(ParkingSession.garage_id == garage_id)
        result = await self.db.execute(query.limit(1))
        return result.scalars().first()

    async def get_by_id_for_update(self, session_id: int, garage_id: int | None = None) -> ParkingSession | None:
        """Fetches a session with a pessimistic lock (FOR UPDATE)."""
        query = select(ParkingSession).where(ParkingSession.id == session_id)
        if garage_id is not None:
            query = query.where(ParkingSession.garage_id == garage_id)

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
        garage_id: int,
        receipt_no: int,
        gate_number: int,
        shift_id: int,
        operator_id: int,
        card_id: int | None = None,
        card_code: str | None = None,
        ticket_code: str | None = None,
        plate_number: str | None = None,
        **kwargs,
    ) -> ParkingSession:
        """Creates a new active session, flushing changes."""
        status = kwargs.pop("status", SessionStatus.ACTIVE)
        entry_time = kwargs.pop("entry_time", datetime.utcnow())
        is_paid = kwargs.pop("is_paid", False)
        is_lost_card = kwargs.pop("is_lost_card", False)

        session = ParkingSession(
            garage_id=garage_id,
            receipt_no=receipt_no,
            card_id=card_id,
            card_code=card_code,
            ticket_code=ticket_code,
            gate_number=gate_number,
            shift_id=shift_id,
            operator_id=operator_id,
            plate_number=plate_number,
            status=status,
            entry_time=entry_time,
            is_paid=is_paid,
            is_lost_card=is_lost_card,
            **kwargs,
        )
        self.db.add(session)
        await self.db.flush()
        return session

    async def get_active_by_plate(self, plate_normalized: str, garage_id: int | None = None) -> list[ParkingSession]:
        """Queries active sessions that match a normalized plate number."""
        query = select(ParkingSession).where(
            ParkingSession.status == SessionStatus.ACTIVE,
            ParkingSession.plate_number == plate_normalized,
        )
        if garage_id is not None:
            query = query.where(ParkingSession.garage_id == garage_id)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_by_shift(
        self, shift_id: int, garage_id: int | None = None, page: int = 1, size: int = 10
    ) -> tuple[list[ParkingSession], int]:
        """Returns a paginated list of sessions for a shift."""
        condition = or_(
            ParkingSession.shift_id == shift_id,
            ParkingSession.exit_shift_id == shift_id,
        )
        if garage_id is not None:
            condition = and_(condition, ParkingSession.garage_id == garage_id)

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
        self, shift_id: int, garage_id: int | None = None, page: int = 1, size: int = 10
    ) -> tuple[list[ParkingSession], int]:
        combined_condition = or_(
            and_(
                ParkingSession.shift_id == shift_id,
                ParkingSession.status == SessionStatus.ACTIVE,
            ),
            and_(
                ParkingSession.exit_shift_id == shift_id,
                ParkingSession.status.in_([SessionStatus.COMPLETED, SessionStatus.LOST_CARD]),
            ),
        )
        if garage_id is not None:
            combined_condition = and_(combined_condition, ParkingSession.garage_id == garage_id)

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
        self, subscription_ids: list[int], garage_id: int | None = None, page: int = 1, size: int = 10
    ) -> tuple[list[ParkingSession], int]:
        if not subscription_ids:
            return [], 0
        condition = ParkingSession.subscription_id.in_(subscription_ids)
        if garage_id is not None:
            condition = and_(condition, ParkingSession.garage_id == garage_id)

        count_query = select(func.count()).select_from(ParkingSession).where(condition)
        count_result = await self.db.execute(count_query)
        total_count = count_result.scalar_one()

        offset_val = (page - 1) * size
        query = select(ParkingSession).where(condition).order_by(ParkingSession.entry_time.desc()).offset(offset_val).limit(size)
        result = await self.db.execute(query)
        return list(result.scalars().all()), total_count

    async def get_by_id(self, session_id: int, garage_id: int | None = None) -> ParkingSession | None:
        """Fetches a session by id without locking."""
        query = select(ParkingSession).where(ParkingSession.id == session_id)
        if garage_id is not None:
            query = query.where(ParkingSession.garage_id == garage_id)
        result = await self.db.execute(query.limit(1))
        return result.scalars().first()

    async def count_by_shift_and_status(
        self, shift_id: int, status: SessionStatus | None = None, garage_id: int | None = None
    ) -> int:
        """Returns the number of sessions in a shift, optionally filtered by status."""
        query = select(func.count()).select_from(ParkingSession).where(ParkingSession.shift_id == shift_id)
        if garage_id is not None:
            query = query.where(ParkingSession.garage_id == garage_id)
        if status is not None:
            query = query.where(ParkingSession.status == status)
        result = await self.db.execute(query)
        return result.scalar_one()

__all__ = ["ParkingSessionRepository"]
