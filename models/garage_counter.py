from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Integer, ForeignKey
from database import Base

class GarageCounter(Base):
    __tablename__ = "garage_counters"

    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.id", ondelete="RESTRICT"), primary_key=True, autoincrement=False)
    last_receipt_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)

    # Relationships
    garage: Mapped["Garage"] = relationship("Garage", back_populates="counter", foreign_keys=[garage_id])

__all__ = ["GarageCounter"]
