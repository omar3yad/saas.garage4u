from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, CheckConstraint
from database import Base
from models.mixins import TimestampMixin

class Owner(Base, TimestampMixin):
    __tablename__ = "owners"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    contact_phone: Mapped[str] = mapped_column(String(30), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    subscription_plan: Mapped[str] = mapped_column(String(40), nullable=False, server_default="STANDARD")
    billing_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="ACTIVE", index=True)
    max_garages_allowed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    # Relationships
    garages: Mapped[list["Garage"]] = relationship("Garage", back_populates="owner", cascade="all, delete-orphan")
    users: Mapped[list["User"]] = relationship("User", back_populates="owner", foreign_keys="[User.owner_id]")

    __table_args__ = (
        CheckConstraint("max_garages_allowed > 0", name="ck_owners_max_garages"),
    )

__all__ = ["Owner"]
