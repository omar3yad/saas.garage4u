from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from models.garage import Garage

class GarageRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, garage_id: int, owner_id: int | None = None) -> Garage | None:
        query = select(Garage).where(Garage.id == garage_id)
        if owner_id is not None:
            query = query.where(Garage.owner_id == owner_id)
        result = await self.db.execute(query.limit(1))
        return result.scalars().first()

    async def get_by_code(self, code: str) -> Garage | None:
        clean = code.strip().upper()
        result = await self.db.execute(select(Garage).where(Garage.code == clean).limit(1))
        return result.scalars().first()

    async def get_by_owner(self, owner_id: int) -> list[Garage]:
        result = await self.db.execute(select(Garage).where(Garage.owner_id == owner_id).order_by(Garage.id.asc()))
        return list(result.scalars().all())

    async def count_by_owner(self, owner_id: int) -> int:
        result = await self.db.execute(select(func.count(Garage.id)).where(Garage.owner_id == owner_id))
        return result.scalar_one_or_none() or 0

    async def create(self, **kwargs) -> Garage:
        if "code" in kwargs:
            kwargs["code"] = kwargs["code"].strip().upper()
        garage = Garage(**kwargs)
        self.db.add(garage)
        await self.db.flush()
        return garage

    async def update(self, garage_id: int, owner_id: int | None = None, **fields) -> Garage | None:
        garage = await self.get_by_id(garage_id, owner_id=owner_id)
        if not garage:
            return None
        for key, value in fields.items():
            if value is not None:
                if key == "code" and isinstance(value, str):
                    value = value.strip().upper()
                setattr(garage, key, value)
        await self.db.flush()
        return garage

    async def get_all(self, page: int = 1, size: int = 20) -> tuple[list[Garage], int]:
        count_stmt = select(func.count()).select_from(Garage)
        select_stmt = select(Garage).offset((page - 1) * size).limit(size).order_by(Garage.id.desc())
        total = (await self.db.execute(count_stmt)).scalar_one()
        res = await self.db.execute(select_stmt)
        return list(res.scalars().all()), total

__all__ = ["GarageRepository"]
