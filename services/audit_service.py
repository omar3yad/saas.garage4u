import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from repositories.audit_log_repo import AuditLogRepository

logger = logging.getLogger(__name__)

class AuditService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def log(
        self,
        actor_id: int | None,
        action: str,
        entity_type: str,
        entity_id: int,
        before: dict | None = None,
        after: dict | None = None,
        actor_type: str = "user",
        garage_id: int | None = None,
        owner_id: int | None = None,
    ) -> None:
        try:
            payload_before = None
            payload_after = None

            if before is not None:
                before_clean = dict(before)
                before_clean.pop("hashed_password", None)
                payload_before = json.dumps(before_clean)

            if after is not None:
                after_clean = dict(after)
                after_clean.pop("hashed_password", None)
                payload_after = json.dumps(after_clean)

            repo = AuditLogRepository(self.db)
            await repo.create(
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
        except Exception as e:
            logger.error("Failed to write audit log: %s", str(e), exc_info=True)
