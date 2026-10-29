from datetime import datetime
from pydantic import BaseModel, Field, model_validator, ConfigDict
from models.user import UserRole

class UserBase(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    username: str = Field(min_length=1, max_length=60, pattern=r"^[a-zA-Z0-9_]+$")
    role: UserRole
    gate_number: int | None = Field(default=None, ge=1, le=10)
    owner_id: int | None = Field(default=None)
    garage_id: int | None = Field(default=None)
    must_change_password: bool = Field(default=False)

    @model_validator(mode="after")
    def validate_role_gate(self) -> "UserBase":
        if self.role == UserRole.OPERATOR:
            if self.gate_number is None:
                raise ValueError("Gate number is required for operators")
        elif self.role in (UserRole.ADMIN, UserRole.MANAGER, UserRole.OWNER, UserRole.SUPER_ADMIN):
            if self.gate_number is not None:
                raise ValueError(f"Gate number must be null for {self.role.value}")
        return self

class UserCreate(UserBase):
    password: str = Field(min_length=4, max_length=128)

class UserResponse(UserBase):
    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class UserUpdatePassword(BaseModel):
    new_password: str = Field(min_length=4, max_length=128)

class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    gate_number: int | None = Field(default=None, ge=1, le=10)
    role: UserRole | None = None
    owner_id: int | None = None
    garage_id: int | None = None
    must_change_password: bool | None = None
    is_active: bool | None = None

__all__ = [
    "UserBase",
    "UserCreate",
    "UserResponse",
    "UserUpdatePassword",
    "UserUpdate",
]
