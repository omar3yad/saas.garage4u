from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Text, ForeignKey, UniqueConstraint
from database import Base
from models.mixins import TimestampMixin

class Subscriber(Base, TimestampMixin):
    __tablename__ = "subscribers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.id", ondelete="RESTRICT"), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    plate_number: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    company_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    card_code: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    garage: Mapped["Garage"] = relationship("Garage", foreign_keys=[garage_id])

    __table_args__ = (
        UniqueConstraint("garage_id", "plate_number", name="uq_garage_subscriber_plate"),
        UniqueConstraint("id", "garage_id", name="uq_subscribers_id_garage"),
    )

__all__ = ["Subscriber"]
