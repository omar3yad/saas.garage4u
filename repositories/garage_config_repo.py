from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from models.garage_config import GarageConfig

class GarageConfigRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_garage_id(self, garage_id: int) -> GarageConfig | None:
        result = await self.db.execute(
            select(GarageConfig).where(GarageConfig.garage_id == garage_id).limit(1)
        )
        return result.scalars().first()

    async def create(self, **kwargs) -> GarageConfig:
        config = GarageConfig(**kwargs)
        self.db.add(config)
        await self.db.flush()
        return config

    async def update_with_optimistic_lock(
        self, garage_id: int, expected_version: int, **fields
    ) -> GarageConfig | None:
        fields["version"] = expected_version + 1
        stmt = (
            update(GarageConfig)
            .where(
                GarageConfig.garage_id == garage_id,
                GarageConfig.version == expected_version,
            )
            .values(**fields)
            .execution_options(synchronize_session="fetch")
        )
        res = await self.db.execute(stmt)
        if res.rowcount == 0:
            return None
        await self.db.flush()
        return await self.get_by_garage_id(garage_id)

__all__ = ["GarageConfigRepository"]
