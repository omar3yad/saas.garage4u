import enum
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import (
    String, SmallInteger, Integer, ForeignKey, Enum, Text, Index, Float, text,
    UniqueConstraint, CheckConstraint, ForeignKeyConstraint
)
from database import Base
from models.mixins import TimestampMixin

class SessionStatus(enum.Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    LOST_CARD = "LOST_CARD"

class PaymentMethod(enum.Enum):
    CASH = "CASH"
    cash = "cash"

class PlateCaptureStatus(str, enum.Enum):
    """Lifecycle of the edge camera capture attached to an entry session.

    Stored as a plain string column so adding states never needs a DB enum migration.
    """
    PENDING = "PENDING"      # trigger dispatched, waiting for the edge PC
    SUCCESS = "SUCCESS"      # plate read and stored
    NO_PLATE = "NO_PLATE"    # image captured but no plate recognised
    FAILED = "FAILED"        # edge reported an error (camera/model)
    TIMEOUT = "TIMEOUT"      # no result before the deadline
    OFFLINE = "OFFLINE"      # no edge PC connected for this gate

    @property
    def is_terminal(self) -> bool:
        return self is not PlateCaptureStatus.PENDING


class ParkingSession(Base, TimestampMixin):
    __tablename__ = "parking_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.id", ondelete="RESTRICT"), nullable=False, index=True)
    receipt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    ticket_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    card_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    card_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[SessionStatus] = mapped_column(Enum(SessionStatus), nullable=False, server_default="ACTIVE", index=True)
    gate_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    shift_id: Mapped[int] = mapped_column(nullable=False, index=True)
    operator_id: Mapped[int] = mapped_column(nullable=False)
    entry_time: Mapped[datetime] = mapped_column(nullable=False)
    exit_time: Mapped[datetime | None] = mapped_column(nullable=True)
    plate_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(nullable=True)
    pricing_rule_id: Mapped[int | None] = mapped_column(nullable=True)
    amount_charged: Mapped[int | None] = mapped_column(nullable=True)
    is_lost_card: Mapped[bool] = mapped_column(nullable=False, server_default="0")
    lost_card_penalty_applied: Mapped[int | None] = mapped_column(nullable=True)
    payment_method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod), nullable=False, server_default="cash")
    is_paid: Mapped[bool] = mapped_column(nullable=False, server_default="0")
    exit_operator_id: Mapped[int | None] = mapped_column(nullable=True)
    exit_shift_id: Mapped[int | None] = mapped_column(nullable=True)
    receipt_printed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    admin_override_by: Mapped[int | None] = mapped_column(nullable=True)
    admin_override_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(nullable=False, server_default="0")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    subscription_id: Mapped[int | None] = mapped_column(nullable=True)
    is_subscribed: Mapped[bool] = mapped_column(nullable=False, server_default="0")
    plate_capture_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    plate_capture_requested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    plate_captured_at: Mapped[datetime | None] = mapped_column(nullable=True)
    plate_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    plate_image_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plate_capture_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    exit_image_path: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    garage: Mapped["Garage"] = relationship("Garage", foreign_keys=[garage_id])
    card: Mapped["ParkingCard | None"] = relationship(
        "ParkingCard",
        foreign_keys=[card_id, garage_id],
        primaryjoin="and_(ParkingSession.card_id==ParkingCard.id, ParkingSession.garage_id==ParkingCard.garage_id)",
        overlaps="garage",
    )
    entry_shift: Mapped["Shift"] = relationship(
        "Shift",
        foreign_keys=[shift_id, garage_id],
        primaryjoin="and_(ParkingSession.shift_id==Shift.id, ParkingSession.garage_id==Shift.garage_id)",
        overlaps="card,garage",
    )
    exit_shift: Mapped["Shift | None"] = relationship(
        "Shift",
        foreign_keys=[exit_shift_id, garage_id],
        primaryjoin="and_(ParkingSession.exit_shift_id==Shift.id, ParkingSession.garage_id==Shift.garage_id)",
        overlaps="card,entry_shift,garage",
    )
    operator: Mapped["User"] = relationship(
        "User",
        foreign_keys=[operator_id, garage_id],
        primaryjoin="and_(ParkingSession.operator_id==User.id, ParkingSession.garage_id==User.garage_id)",
        overlaps="card,entry_shift,exit_shift,garage",
    )
    exit_operator: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[exit_operator_id, garage_id],
        primaryjoin="and_(ParkingSession.exit_operator_id==User.id, ParkingSession.garage_id==User.garage_id)",
        overlaps="card,entry_shift,exit_shift,garage,operator",
    )
    pricing_rule: Mapped["PricingRule | None"] = relationship(
        "PricingRule",
        foreign_keys=[pricing_rule_id, garage_id],
        primaryjoin="and_(ParkingSession.pricing_rule_id==PricingRule.id, ParkingSession.garage_id==PricingRule.garage_id)",
        overlaps="card,entry_shift,exit_operator,exit_shift,garage,operator",
    )

    __table_args__ = (
        CheckConstraint(
            "((card_id IS NOT NULL AND card_code IS NOT NULL) OR (ticket_code IS NOT NULL))",
            name="ck_session_card_or_ticket",
        ),
        UniqueConstraint("garage_id", "ticket_code", name="uq_garage_ticket_code"),
        UniqueConstraint("garage_id", "receipt_no", name="uq_garage_receipt_no"),
        ForeignKeyConstraint(["card_id", "garage_id"], ["parking_cards.id", "parking_cards.garage_id"], name="fk_sessions_card_garage", ondelete="RESTRICT"),
        ForeignKeyConstraint(["shift_id", "garage_id"], ["shifts.id", "shifts.garage_id"], name="fk_sessions_entry_shift_garage", ondelete="RESTRICT"),
        ForeignKeyConstraint(["exit_shift_id", "garage_id"], ["shifts.id", "shifts.garage_id"], name="fk_sessions_exit_shift_garage", ondelete="RESTRICT"),
        ForeignKeyConstraint(["pricing_rule_id", "garage_id"], ["pricing_rules.id", "pricing_rules.garage_id"], name="fk_sessions_pricing_garage", ondelete="RESTRICT"),
        ForeignKeyConstraint(["operator_id", "garage_id"], ["users.id", "users.garage_id"], name="fk_sessions_operator_garage", ondelete="RESTRICT"),
        ForeignKeyConstraint(["exit_operator_id", "garage_id"], ["users.id", "users.garage_id"], name="fk_sessions_exit_operator_garage", ondelete="RESTRICT"),
        ForeignKeyConstraint(["admin_override_by", "garage_id"], ["users.id", "users.garage_id"], name="fk_sessions_admin_override_garage", ondelete="RESTRICT"),
        ForeignKeyConstraint(["garage_id", "gate_number"], ["garage_gates.garage_id", "garage_gates.gate_number"], name="fk_sessions_gate_garage", ondelete="RESTRICT"),
        ForeignKeyConstraint(["subscription_id", "garage_id"], ["subscriptions.id", "subscriptions.garage_id"], name="fk_sessions_subscription_garage", ondelete="RESTRICT"),
        Index(
            "idx_garage_active_card_id",
            "garage_id",
            "card_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE' AND card_id IS NOT NULL"),
        ),
    )

__all__ = ["SessionStatus", "PaymentMethod", "PlateCaptureStatus", "ParkingSession"]
