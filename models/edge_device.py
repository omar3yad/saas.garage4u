from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, SmallInteger, ForeignKey, UniqueConstraint, CheckConstraint, ForeignKeyConstraint, func
from database import Base
from models.mixins import TimestampMixin

class EdgeDevice(Base, TimestampMixin):
    __tablename__ = "edge_devices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.id", ondelete="RESTRICT"), nullable=False, index=True)
    device_name: Mapped[str] = mapped_column(String(80), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Relationships
    garage: Mapped["Garage"] = relationship("Garage", foreign_keys=[garage_id])
    assigned_gates: Mapped[list["EdgeDeviceGate"]] = relationship("EdgeDeviceGate", back_populates="device", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("id", "garage_id", name="uq_edge_devices_id_garage"),
    )

class EdgeDeviceGate(Base):
    __tablename__ = "edge_device_gates"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(nullable=False)
    garage_id: Mapped[int] = mapped_column(nullable=False)
    gate_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    direction: Mapped[str] = mapped_column(String(15), nullable=False, server_default="BIDIRECTIONAL")
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default="1", default=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    # Relationships
    device: Mapped["EdgeDevice"] = relationship(
        "EdgeDevice",
        back_populates="assigned_gates",
        foreign_keys=[device_id, garage_id],
        primaryjoin="and_(EdgeDeviceGate.device_id==EdgeDevice.id, EdgeDeviceGate.garage_id==EdgeDevice.garage_id)",
    )

    __table_args__ = (
        CheckConstraint("direction IN ('ENTRY', 'EXIT', 'BIDIRECTIONAL')", name="ck_edge_device_gates_direction"),
        UniqueConstraint("device_id", "garage_id", "gate_number", "direction", name="uq_device_gate_direction"),
        UniqueConstraint("garage_id", "gate_number", "direction", name="uq_gate_direction_active"),
        ForeignKeyConstraint(["device_id", "garage_id"], ["edge_devices.id", "edge_devices.garage_id"], name="fk_edg_device", ondelete="RESTRICT"),
        ForeignKeyConstraint(["garage_id", "gate_number"], ["garage_gates.garage_id", "garage_gates.gate_number"], name="fk_edg_gate", ondelete="RESTRICT"),
    )

__all__ = ["EdgeDevice", "EdgeDeviceGate"]
