from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from models.owner import Owner
from models.garage import Garage

class OwnerRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, owner_id: int) -> Owner | None:
        return await self.db.get(Owner, owner_id)

    async def get_by_email(self, email: str) -> Owner | None:
        clean = email.strip().lower()
        result = await self.db.execute(select(Owner).where(Owner.contact_email == clean).limit(1))
        return result.scalars().first()

    async def count_garages(self, owner_id: int) -> int:
        result = await self.db.execute(select(func.count(Garage.id)).where(Garage.owner_id == owner_id))
        return result.scalar_one_or_none() or 0

    async def can_add_garage(self, owner_id: int) -> bool:
        owner = await self.get_by_id(owner_id)
        if not owner or owner.billing_status not in ("ACTIVE", "TRIAL"):
            return False
        current_count = await self.count_garages(owner_id)
        return current_count < owner.max_garages_allowed

    async def create(self, **kwargs) -> Owner:
        if "contact_email" in kwargs:
            kwargs["contact_email"] = kwargs["contact_email"].strip().lower()
        owner = Owner(**kwargs)
        self.db.add(owner)
        await self.db.flush()
        return owner

    async def update(self, owner_id: int, **fields) -> Owner | None:
        owner = await self.get_by_id(owner_id)
        if not owner:
            return None
        for key, value in fields.items():
            if value is not None:
                if key == "contact_email" and isinstance(value, str):
                    value = value.strip().lower()
                setattr(owner, key, value)
        await self.db.flush()
        return owner

    async def get_all(self, page: int = 1, size: int = 20) -> tuple[list[Owner], int]:
        count_stmt = select(func.count()).select_from(Owner)
        select_stmt = select(Owner).offset((page - 1) * size).limit(size).order_by(Owner.id.desc())
        count_res = await self.db.execute(count_stmt)
        total = count_res.scalar_one()
        res = await self.db.execute(select_stmt)
        return list(res.scalars().all()), total

__all__ = ["OwnerRepository"]
