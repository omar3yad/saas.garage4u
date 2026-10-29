from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, SmallInteger, ForeignKey, UniqueConstraint, CheckConstraint
from database import Base
from models.mixins import TimestampMixin

class GarageGate(Base, TimestampMixin):
    __tablename__ = "garage_gates"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.id", ondelete="RESTRICT"), nullable=False)
    gate_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    gate_type: Mapped[str] = mapped_column(String(30), nullable=False, server_default="MANUAL_BIDIRECTIONAL")
    default_terminal_profile: Mapped[str] = mapped_column(String(30), nullable=False, server_default="DESKTOP_PC")
    paper_width_mm: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="80")

    # Relationships
    garage: Mapped["Garage"] = relationship("Garage", back_populates="gates", foreign_keys=[garage_id])

    __table_args__ = (
        CheckConstraint("gate_number BETWEEN 1 AND 10", name="ck_garage_gates_number"),
        CheckConstraint("paper_width_mm IN (58, 80)", name="ck_garage_gates_paper_width"),
        UniqueConstraint("garage_id", "gate_number", name="uq_garage_gate_number"),
        UniqueConstraint("id", "garage_id", name="uq_garage_gates_id_garage"),
    )

__all__ = ["GarageGate"]
