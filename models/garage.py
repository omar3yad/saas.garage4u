from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, SmallInteger, Text, ForeignKey, UniqueConstraint, CheckConstraint
from database import Base
from models.mixins import TimestampMixin

class Garage(Base, TimestampMixin):
    __tablename__ = "garages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owners.id", ondelete="RESTRICT"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str] = mapped_column(String(60), nullable=False, server_default="Cairo")
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, server_default="Africa/Cairo")
    currency: Mapped[str] = mapped_column(String(10), nullable=False, server_default="EGP")
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="ACTIVE", index=True)

    can_use_anpr: Mapped[bool] = mapped_column(nullable=False, server_default="0", default=False)
    can_use_subscriptions: Mapped[bool] = mapped_column(nullable=False, server_default="1", default=True)
    max_gates: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="2")

    # Relationships
    owner: Mapped["Owner"] = relationship("Owner", back_populates="garages", foreign_keys=[owner_id])
    gates: Mapped[list["GarageGate"]] = relationship("GarageGate", back_populates="garage", cascade="all, delete-orphan")
    config: Mapped["GarageConfig | None"] = relationship("GarageConfig", back_populates="garage", uselist=False, cascade="all, delete-orphan")
    users: Mapped[list["User"]] = relationship("User", back_populates="garage", foreign_keys="[User.garage_id]")
    counter: Mapped["GarageCounter | None"] = relationship("GarageCounter", back_populates="garage", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("capacity > 0", name="ck_garages_capacity"),
        CheckConstraint("status IN ('ACTIVE', 'SUSPENDED', 'TERMINATED')", name="ck_garages_status"),
        CheckConstraint("max_gates BETWEEN 1 AND 10", name="ck_garages_max_gates"),
        UniqueConstraint("id", "owner_id", name="uq_garages_id_owner"),
    )

__all__ = ["Garage"]
