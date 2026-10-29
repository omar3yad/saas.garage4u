from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, ForeignKey, Text, func, CheckConstraint
from database import Base

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="user")
    garage_id: Mapped[int | None] = mapped_column(ForeignKey("garages.id", ondelete="RESTRICT"), nullable=True, index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("owners.id", ondelete="RESTRICT"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[int] = mapped_column(nullable=False)
    payload_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    # Relationships
    actor: Mapped["User | None"] = relationship("User", foreign_keys=[actor_id])
    garage: Mapped["Garage | None"] = relationship("Garage", foreign_keys=[garage_id])
    owner: Mapped["Owner | None"] = relationship("Owner", foreign_keys=[owner_id])

    __table_args__ = (
        CheckConstraint(
            "((garage_id IS NOT NULL) OR "
            "(garage_id IS NULL AND ("
            "entity_type IN ('platform', 'owner_account', 'system') OR "
            "actor_type = 'system' OR "
            "action IN ('USER_LOGIN', 'USER_LOGOUT', 'LOGIN_FAILED', 'SUPER_ADMIN_GARAGE_ACCESS')"
            ")))",
            name="ck_audit_garage_context",
        ),
    )

__all__ = ["AuditLog"]
