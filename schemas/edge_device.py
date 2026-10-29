from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

class EdgeDeviceCreate(BaseModel):
    device_name: str = Field(min_length=1, max_length=80)

class EdgeDeviceResponse(BaseModel):
    id: int
    garage_id: int
    device_name: str
    token_hash: str
    revoked_at: datetime | None = None
    last_seen_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class EdgeDeviceTokenResponse(BaseModel):
    device: EdgeDeviceResponse
    raw_token: str

class EdgeDeviceGateAssign(BaseModel):
    gate_number: int = Field(ge=1, le=10)
    direction: str = Field(default="BIDIRECTIONAL", pattern=r"^(ENTRY|EXIT|BIDIRECTIONAL)$")
    is_active: bool = True

class EdgeDeviceGateResponse(BaseModel):
    id: int
    device_id: int
    garage_id: int
    gate_number: int
    direction: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

__all__ = [
    "EdgeDeviceCreate",
    "EdgeDeviceResponse",
    "EdgeDeviceTokenResponse",
    "EdgeDeviceGateAssign",
    "EdgeDeviceGateResponse",
]
