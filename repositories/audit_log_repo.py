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
