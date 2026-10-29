from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from models.garage_counter import GarageCounter

class GarageCounterRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_next_receipt_no(self, garage_id: int) -> int:
        """Executes atomic increment:
        UPDATE garage_counters SET last_receipt_no = last_receipt_no + 1
        WHERE garage_id = :gid RETURNING last_receipt_no;
        """
        stmt = text(
            "UPDATE garage_counters "
            "SET last_receipt_no = last_receipt_no + 1 "
            "WHERE garage_id = :gid "
            "RETURNING last_receipt_no;"
        )
        res = await self.db.execute(stmt, {"gid": garage_id})
        val = res.scalar_one_or_none()
        if val is None:
            # If counter row was missing for this garage, initialize it atomically
            init_stmt = text(
                "INSERT INTO garage_counters (garage_id, last_receipt_no) "
                "VALUES (:gid, 1) "
                "ON CONFLICT (garage_id) DO UPDATE SET last_receipt_no = garage_counters.last_receipt_no + 1 "
                "RETURNING last_receipt_no;"
            )
            res2 = await self.db.execute(init_stmt, {"gid": garage_id})
            val = res2.scalar_one()
        return val

    async def get_current(self, garage_id: int) -> int:
        stmt = text("SELECT last_receipt_no FROM garage_counters WHERE garage_id = :gid;")
        res = await self.db.execute(stmt, {"gid": garage_id})
        return res.scalar_one_or_none() or 0

__all__ = ["GarageCounterRepository"]
