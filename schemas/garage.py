from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

class GarageBase(BaseModel):
    owner_id: int
    code: str = Field(min_length=2, max_length=30, pattern=r"^[A-Z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=150)
    address: str | None = None
    city: str = Field(default="Cairo", max_length=60)
    capacity: int = Field(gt=0)
    timezone: str = Field(default="Africa/Cairo", max_length=50)
    currency: str = Field(default="EGP", max_length=10)
    status: str = Field(default="ACTIVE", pattern=r"^(ACTIVE|SUSPENDED|TERMINATED)$")
    can_use_anpr: bool = False
    can_use_subscriptions: bool = True
    max_gates: int = Field(default=2, ge=1, le=10)

class GarageCreate(GarageBase):
    pass

class GarageUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    address: str | None = None
    city: str | None = Field(default=None, max_length=60)
    capacity: int | None = Field(default=None, gt=0)
    timezone: str | None = Field(default=None, max_length=50)
    currency: str | None = Field(default=None, max_length=10)

class GarageEntitlementsUpdate(BaseModel):
    can_use_anpr: bool | None = None
    can_use_subscriptions: bool | None = None
    max_gates: int | None = Field(default=None, ge=1, le=10)
    status: str | None = Field(default=None, pattern=r"^(ACTIVE|SUSPENDED|TERMINATED)$")

class GarageResponse(GarageBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

__all__ = [
    "GarageBase",
    "GarageCreate",
    "GarageUpdate",
    "GarageEntitlementsUpdate",
    "GarageResponse",
]
