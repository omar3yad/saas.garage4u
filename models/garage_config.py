from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, SmallInteger, ForeignKey, UniqueConstraint, CheckConstraint, JSON
from sqlalchemy.dialects.postgresql import JSONB
from database import Base
from models.mixins import TimestampMixin

class GarageConfig(Base, TimestampMixin):
    __tablename__ = "garage_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.id", ondelete="RESTRICT"), unique=True, nullable=False, index=True)
    ticket_system: Mapped[str] = mapped_column(String(30), nullable=False, server_default="REUSABLE_CARDS")
    anpr_enabled: Mapped[bool] = mapped_column(nullable=False, server_default="0", default=False)
    exit_photo_enabled: Mapped[bool] = mapped_column(nullable=False, server_default="0", default=False)
    subscriptions_enabled: Mapped[bool] = mapped_column(nullable=False, server_default="1", default=True)
    require_shift_cash_reconciliation: Mapped[bool] = mapped_column(nullable=False, server_default="1", default=True)
    min_session_duration_seconds: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="60")
    receipt_settings: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        server_default='{"header_title": "جراج سيارات", "header_subtitle": "أهلاً بكم", "footer_terms": "الجراج غير مسؤول عن محتويات السيارات الداخلية", "show_qr_code": true}',
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1", default=1)

    # Relationships
    garage: Mapped["Garage"] = relationship("Garage", back_populates="config", foreign_keys=[garage_id])

    __table_args__ = (
        CheckConstraint("min_session_duration_seconds BETWEEN 30 AND 600", name="ck_garage_configs_min_duration"),
        UniqueConstraint("id", "garage_id", name="uq_garage_configs_id_garage"),
    )

__all__ = ["GarageConfig"]
