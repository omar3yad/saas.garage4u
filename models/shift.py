from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import SmallInteger, ForeignKey, Text, UniqueConstraint, ForeignKeyConstraint
from database import Base
from models.mixins import TimestampMixin

class Shift(Base, TimestampMixin):
    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.id", ondelete="RESTRICT"), nullable=False, index=True)
    operator_id: Mapped[int] = mapped_column(nullable=False)
    gate_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    opening_cash_egp: Mapped[int] = mapped_column(nullable=False, server_default="0")
    closing_cash_egp: Mapped[int | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_override_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    garage: Mapped["Garage"] = relationship("Garage", foreign_keys=[garage_id])
    operator: Mapped["User"] = relationship(
        "User",
        foreign_keys=[operator_id, garage_id],
        primaryjoin="and_(Shift.operator_id==User.id, Shift.garage_id==User.garage_id)",
        overlaps="garage",
    )

    __table_args__ = (
        UniqueConstraint("id", "garage_id", name="uq_shifts_id_garage"),
        ForeignKeyConstraint(["operator_id", "garage_id"], ["users.id", "users.garage_id"], name="fk_shifts_operator_garage", ondelete="RESTRICT"),
        ForeignKeyConstraint(["garage_id", "gate_number"], ["garage_gates.garage_id", "garage_gates.gate_number"], name="fk_shifts_gate_garage", ondelete="RESTRICT"),
    )

__all__ = ["Shift"]
