import enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Enum, SmallInteger, CheckConstraint, ForeignKey, UniqueConstraint, ForeignKeyConstraint
from database import Base
from models.mixins import TimestampMixin

class UserRole(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    OWNER = "OWNER"
    MANAGER = "MANAGER"
    OPERATOR = "OPERATOR"
    ADMIN = "ADMIN"  # Retained as known technical debt (B4) for legacy backward compatibility

class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    username: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="userrole"), nullable=False)
    gate_number: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default="1")

    owner_id: Mapped[int | None] = mapped_column(ForeignKey("owners.id", ondelete="RESTRICT"), nullable=True)
    garage_id: Mapped[int | None] = mapped_column(ForeignKey("garages.id", ondelete="RESTRICT"), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(nullable=False, server_default="0", default=False)

    # Relationships
    owner: Mapped["Owner | None"] = relationship("Owner", back_populates="users", foreign_keys=[owner_id])
    garage: Mapped["Garage | None"] = relationship("Garage", back_populates="users", foreign_keys=[garage_id])

    __table_args__ = (
        CheckConstraint(
            "(role = 'SUPER_ADMIN' AND owner_id IS NULL AND garage_id IS NULL AND gate_number IS NULL) OR "
            "(role = 'OWNER' AND owner_id IS NOT NULL AND garage_id IS NULL AND gate_number IS NULL) OR "
            "(role = 'MANAGER' AND owner_id IS NOT NULL AND garage_id IS NOT NULL AND gate_number IS NULL) OR "
            "(role = 'OPERATOR' AND owner_id IS NOT NULL AND garage_id IS NOT NULL AND gate_number IS NOT NULL AND gate_number BETWEEN 1 AND 10)",
            name="ck_users_saas_hierarchy",
        ),
        UniqueConstraint("id", "garage_id", name="uq_users_id_garage"),
        UniqueConstraint("garage_id", "username", name="uq_users_garage_username"),
        ForeignKeyConstraint(["garage_id", "owner_id"], ["garages.id", "garages.owner_id"], name="fk_users_garage_owner", ondelete="RESTRICT"),
    )

__all__ = ["UserRole", "User"]
