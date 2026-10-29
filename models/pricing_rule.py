from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, SmallInteger, ForeignKey, UniqueConstraint, ForeignKeyConstraint
from database import Base
from models.mixins import TimestampMixin

class PricingRule(Base, TimestampMixin):
    __tablename__ = "pricing_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.id", ondelete="RESTRICT"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    rate_per_hour: Mapped[int] = mapped_column(nullable=False)
    first_hour_charge: Mapped[int] = mapped_column(nullable=False, server_default="0")
    subsequent_hour_charge: Mapped[int] = mapped_column(nullable=False, server_default="0")
    minimum_charge: Mapped[int] = mapped_column(nullable=False, server_default="0")
    grace_period_mins: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="15")
    lost_card_penalty: Mapped[int] = mapped_column(nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default="0")
    created_by: Mapped[int] = mapped_column(nullable=False)
    effective_from: Mapped[datetime] = mapped_column(nullable=False)
    effective_until: Mapped[datetime | None] = mapped_column(nullable=True)

    # Relationships
    garage: Mapped["Garage"] = relationship("Garage", foreign_keys=[garage_id])
    creator: Mapped["User"] = relationship(
        "User",
        foreign_keys=[created_by, garage_id],
        primaryjoin="and_(PricingRule.created_by==User.id, PricingRule.garage_id==User.garage_id)",
        overlaps="garage",
    )

    __table_args__ = (
        UniqueConstraint("garage_id", "label", name="uq_garage_pricing_label"),
        UniqueConstraint("id", "garage_id", name="uq_pricing_rules_id_garage"),
        ForeignKeyConstraint(["created_by", "garage_id"], ["users.id", "users.garage_id"], name="fk_pricing_created_by", ondelete="RESTRICT"),
    )

__all__ = ["PricingRule"]
