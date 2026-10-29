from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from models.garage_gate import GarageGate

class GarageGateRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, gate_id: int, garage_id: int | None = None) -> GarageGate | None:
        query = select(GarageGate).where(GarageGate.id == gate_id)
        if garage_id is not None:
            query = query.where(GarageGate.garage_id == garage_id)
        result = await self.db.execute(query.limit(1))
        return result.scalars().first()

    async def get_by_number(self, garage_id: int, gate_number: int) -> GarageGate | None:
        result = await self.db.execute(
            select(GarageGate).where(
                GarageGate.garage_id == garage_id,
                GarageGate.gate_number == gate_number,
            ).limit(1)
        )
        return result.scalars().first()

    async def get_all_by_garage(self, garage_id: int) -> list[GarageGate]:
        result = await self.db.execute(
            select(GarageGate).where(GarageGate.garage_id == garage_id).order_by(GarageGate.gate_number.asc())
        )
        return list(result.scalars().all())

    async def count_by_garage(self, garage_id: int) -> int:
        result = await self.db.execute(
            select(func.count(GarageGate.id)).where(GarageGate.garage_id == garage_id)
        )
        return result.scalar_one_or_none() or 0

    async def create(self, **kwargs) -> GarageGate:
        gate = GarageGate(**kwargs)
        self.db.add(gate)
        await self.db.flush()
        return gate

    async def update(self, gate_id: int, garage_id: int, **fields) -> GarageGate | None:
        gate = await self.get_by_id(gate_id, garage_id=garage_id)
        if not gate:
            return None
        for key, value in fields.items():
            if value is not None:
                setattr(gate, key, value)
        await self.db.flush()
        return gate

__all__ = ["GarageGateRepository"]
