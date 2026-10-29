from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

EMAIL_REGEX = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"

class OwnerBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    company_name: str | None = Field(default=None, max_length=150)
    contact_phone: str = Field(min_length=7, max_length=30)
    contact_email: str = Field(max_length=120, pattern=EMAIL_REGEX)
    subscription_plan: str = Field(default="STANDARD", max_length=40)
    billing_status: str = Field(default="ACTIVE", max_length=30)
    max_garages_allowed: int = Field(default=1, ge=1)

class OwnerCreate(OwnerBase):
    pass

class OwnerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    company_name: str | None = Field(default=None, max_length=150)
    contact_phone: str | None = Field(default=None, min_length=7, max_length=30)
    contact_email: str | None = Field(default=None, max_length=120, pattern=EMAIL_REGEX)
    subscription_plan: str | None = Field(default=None, max_length=40)
    billing_status: str | None = Field(default=None, max_length=30)
    max_garages_allowed: int | None = Field(default=None, ge=1)

class OwnerResponse(OwnerBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

__all__ = [
    "OwnerBase",
    "OwnerCreate",
    "OwnerUpdate",
    "OwnerResponse",
]
