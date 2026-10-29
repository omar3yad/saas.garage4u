from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from models.shift import Shift

class ShiftRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, shift_id: int, garage_id: int | None = None) -> Shift | None:
        """Fetches a shift by its id (optionally scoped to a garage)."""
        query = select(Shift).where(Shift.id == shift_id)
        if garage_id is not None:
            query = query.where(Shift.garage_id == garage_id)
        result = await self.db.execute(query.limit(1))
        return result.scalars().first()

    async def get_active_for_operator(self, operator_id: int, garage_id: int | None = None) -> Shift | None:
        """Queries for the active (not ended) shift of an operator in a garage."""
        query = select(Shift).where(
            Shift.operator_id == operator_id,
            Shift.ended_at.is_(None)
        )
        if garage_id is not None:
            query = query.where(Shift.garage_id == garage_id)
        result = await self.db.execute(query.limit(1))
        return result.scalars().first()

    async def create(self, **kwargs) -> Shift:
        """Creates a shift with the given attributes, flushing changes."""
        shift = Shift(**kwargs)
        self.db.add(shift)
        await self.db.flush()
        return shift

    async def get_all(
        self,
        garage_id: int | None = None,
        operator_id: int | None = None,
        gate_number: int | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[Shift], int]:
        """Returns a paginated list of shifts and total count."""
        query = select(Shift)
        count_query = select(func.count()).select_from(Shift)

        if garage_id is not None:
            query = query.where(Shift.garage_id == garage_id)
            count_query = count_query.where(Shift.garage_id == garage_id)

        if operator_id is not None:
            query = query.where(Shift.operator_id == operator_id)
            count_query = count_query.where(Shift.operator_id == operator_id)

        if gate_number is not None:
            query = query.where(Shift.gate_number == gate_number)
            count_query = count_query.where(Shift.gate_number == gate_number)

        count_result = await self.db.execute(count_query)
        total_count = count_result.scalar_one()

        offset_val = (page - 1) * size
        query = query.order_by(Shift.started_at.desc()).offset(offset_val).limit(size)
        result = await self.db.execute(query)
        shifts = list(result.scalars().all())

        return shifts, total_count

__all__ = ["ShiftRepository"]
