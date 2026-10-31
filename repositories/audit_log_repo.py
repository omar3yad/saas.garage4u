from sqlalchemy.ext.asyncio import AsyncSession
from models.audit_log import AuditLog

class AuditLogRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        actor_id: int | None,
        action: str,
        entity_type: str,
        entity_id: int,
        payload_before: str | None = None,
        payload_after: str | None = None,
        actor_type: str = "user",
        garage_id: int | None = None,
        owner_id: int | None = None,
    ) -> AuditLog:
        log_entry = AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_before=payload_before,
            payload_after=payload_after,
            actor_type=actor_type,
            garage_id=garage_id,
            owner_id=owner_id,
        )
        self.db.add(log_entry)
        await self.db.flush()
        return log_entry

    async def get_all(
        self,
        garage_id: int | None = None,
        owner_id: int | None = None,
        action: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[AuditLog], int]:
        from sqlalchemy import select, func, desc
        query = select(AuditLog)
        count_query = select(func.count()).select_from(AuditLog)

        if garage_id is not None:
            query = query.where(AuditLog.garage_id == garage_id)
            count_query = count_query.where(AuditLog.garage_id == garage_id)
        if owner_id is not None:
            query = query.where(AuditLog.owner_id == owner_id)
            count_query = count_query.where(AuditLog.owner_id == owner_id)
        if action is not None:
            query = query.where(AuditLog.action == action)
            count_query = count_query.where(AuditLog.action == action)

        count_res = await self.db.execute(count_query)
        total = count_res.scalar_one()

        offset = (page - 1) * size
        query = query.order_by(desc(AuditLog.id)).offset(offset).limit(size)
        res = await self.db.execute(query)
        return list(res.scalars().all()), total
