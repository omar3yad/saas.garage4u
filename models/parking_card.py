import enum
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Enum, ForeignKey, UniqueConstraint
from database import Base
from models.mixins import TimestampMixin

class CardStatus(enum.Enum):
    AVAILABLE = "available"
    IN_USE = "in_use"
    LOST = "lost"
    DAMAGED = "damaged"

class ParkingCard(Base, TimestampMixin):
    __tablename__ = "parking_cards"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.id", ondelete="RESTRICT"), nullable=False, index=True)
    card_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[CardStatus] = mapped_column(Enum(CardStatus), nullable=False, server_default="available")
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Relationships
    garage: Mapped["Garage"] = relationship("Garage", foreign_keys=[garage_id])

    __table_args__ = (
        UniqueConstraint("garage_id", "card_code", name="uq_garage_card_code"),
        UniqueConstraint("id", "garage_id", name="uq_parking_cards_id_garage"),
    )

__all__ = ["CardStatus", "ParkingCard"]
