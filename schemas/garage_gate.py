from datetime import datetime
from pydantic import BaseModel, Field, model_validator, ConfigDict

class GarageGateBase(BaseModel):
    gate_number: int = Field(ge=1, le=10)
    name: str = Field(min_length=1, max_length=60)
    gate_type: str = Field(default="MANUAL_BIDIRECTIONAL", pattern=r"^(ENTRY_ONLY|EXIT_ONLY|COMBINED|MANUAL_BIDIRECTIONAL)$")
    default_terminal_profile: str = Field(default="DESKTOP_PC", pattern=r"^(DESKTOP_PC|SUNMI_POS|MOBILE_WEB|DESKTOP_GATE_KIOSK)$")
    paper_width_mm: int = Field(default=80)

    @model_validator(mode="after")
    def validate_paper_width(self) -> "GarageGateBase":
        if self.paper_width_mm not in (58, 80):
            raise ValueError("paper_width_mm must be 58 or 80")
        return self

class GarageGateCreate(GarageGateBase):
    pass

class GarageGateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    gate_type: str | None = Field(default=None, pattern=r"^(ENTRY_ONLY|EXIT_ONLY|COMBINED|MANUAL_BIDIRECTIONAL)$")
    default_terminal_profile: str | None = Field(default=None, pattern=r"^(DESKTOP_PC|SUNMI_POS|MOBILE_WEB|DESKTOP_GATE_KIOSK)$")
    paper_width_mm: int | None = None

    @model_validator(mode="after")
    def validate_paper_width_update(self) -> "GarageGateUpdate":
        if self.paper_width_mm is not None and self.paper_width_mm not in (58, 80):
            raise ValueError("paper_width_mm must be 58 or 80")
        return self

class GarageGateResponse(GarageGateBase):
    id: int
    garage_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

__all__ = [
    "GarageGateBase",
    "GarageGateCreate",
    "GarageGateUpdate",
    "GarageGateResponse",
]
