from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, SmallInteger, Integer, ForeignKey, Text, UniqueConstraint, ForeignKeyConstraint
from database import Base
from models.mixins import TimestampMixin

class SubscriptionPlan(Base, TimestampMixin):
    __tablename__ = "subscription_plans"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.id", ondelete="RESTRICT"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    duration_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    price_piastres: Mapped[int] = mapped_column(Integer, nullable=False)
    max_entries_per_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default="1")
    created_by: Mapped[int] = mapped_column(nullable=False)

    # Relationships
    garage: Mapped["Garage"] = relationship("Garage", foreign_keys=[garage_id])
    creator: Mapped["User"] = relationship(
        "User",
        foreign_keys=[created_by, garage_id],
        primaryjoin="and_(SubscriptionPlan.created_by==User.id, SubscriptionPlan.garage_id==User.garage_id)",
        overlaps="garage",
    )

    __table_args__ = (
        UniqueConstraint("garage_id", "label", name="uq_garage_plan_label"),
        UniqueConstraint("id", "garage_id", name="uq_subscription_plans_id_garage"),
        ForeignKeyConstraint(["created_by", "garage_id"], ["users.id", "users.garage_id"], name="fk_plans_created_by", ondelete="RESTRICT"),
    )

__all__ = ["SubscriptionPlan"]
