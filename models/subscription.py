import enum
from datetime import date, datetime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import (
    String, SmallInteger, Integer, Date, ForeignKey, Enum, Text,
    UniqueConstraint, ForeignKeyConstraint
)
from database import Base
from models.mixins import TimestampMixin

class SubscriptionStatus(enum.Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    PENDING = "PENDING"

class Subscription(Base, TimestampMixin):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.id", ondelete="RESTRICT"), nullable=False, index=True)
    subscriber_id: Mapped[int] = mapped_column(nullable=False, index=True)
    plan_id: Mapped[int] = mapped_column(nullable=False)
    card_id: Mapped[int] = mapped_column(nullable=False, index=True)
    plate_number: Mapped[str] = mapped_column(String(30), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscriptionstatus"),
        nullable=False,
        server_default="ACTIVE",
        index=True,
    )
    amount_paid_piastres: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_price_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(nullable=True)
    collected_by: Mapped[int | None] = mapped_column(nullable=True)
    renewal_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    previous_subscription_id: Mapped[int | None] = mapped_column(nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    garage: Mapped["Garage"] = relationship("Garage", foreign_keys=[garage_id])
    subscriber: Mapped["Subscriber"] = relationship(
        "Subscriber",
        foreign_keys=[subscriber_id, garage_id],
        primaryjoin="and_(Subscription.subscriber_id==Subscriber.id, Subscription.garage_id==Subscriber.garage_id)",
        overlaps="garage",
    )
    plan: Mapped["SubscriptionPlan"] = relationship(
        "SubscriptionPlan",
        foreign_keys=[plan_id, garage_id],
        primaryjoin="and_(Subscription.plan_id==SubscriptionPlan.id, Subscription.garage_id==SubscriptionPlan.garage_id)",
        overlaps="garage,subscriber",
    )
    card: Mapped["ParkingCard"] = relationship(
        "ParkingCard",
        foreign_keys=[card_id, garage_id],
        primaryjoin="and_(Subscription.card_id==ParkingCard.id, Subscription.garage_id==ParkingCard.garage_id)",
        overlaps="garage,plan,subscriber",
    )
    collector: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[collected_by, garage_id],
        primaryjoin="and_(Subscription.collected_by==User.id, Subscription.garage_id==User.garage_id)",
        overlaps="card,garage,plan,subscriber",
    )

    @property
    def days_remaining(self) -> int:
        from utils.time import cairo_now
        if not self.end_date:
            return 0
        today = cairo_now().date()
        return max((self.end_date - today).days, 0)

    __table_args__ = (
        UniqueConstraint("id", "garage_id", name="uq_subscriptions_id_garage"),
        ForeignKeyConstraint(["subscriber_id", "garage_id"], ["subscribers.id", "subscribers.garage_id"], name="fk_subscriptions_subscriber_garage", ondelete="RESTRICT"),
        ForeignKeyConstraint(["plan_id", "garage_id"], ["subscription_plans.id", "subscription_plans.garage_id"], name="fk_subscriptions_plan_garage", ondelete="RESTRICT"),
        ForeignKeyConstraint(["card_id", "garage_id"], ["parking_cards.id", "parking_cards.garage_id"], name="fk_subscriptions_card_garage", ondelete="RESTRICT"),
        ForeignKeyConstraint(["collected_by", "garage_id"], ["users.id", "users.garage_id"], name="fk_subscriptions_collected_by", ondelete="RESTRICT"),
        ForeignKeyConstraint(["previous_subscription_id", "garage_id"], ["subscriptions.id", "subscriptions.garage_id"], name="fk_subscriptions_prev_sub", ondelete="RESTRICT"),
    )

__all__ = ["SubscriptionStatus", "Subscription"]
