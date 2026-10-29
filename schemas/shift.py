from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

class ShiftOpenRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    opening_cash_egp: int = Field(ge=0)

class ShiftCloseRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    closing_cash_egp: int = Field(ge=0)

class ShiftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    garage_id: int
    operator_id: int
    gate_number: int
    started_at: datetime
    ended_at: datetime | None
    opening_cash_egp: int
    closing_cash_egp: int | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

class ShiftSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    shift_id: int
    garage_id: int
    operator_id: int
    gate_number: int
    started_at: datetime
    ended_at: datetime | None
    total_sessions: int
    completed_sessions: int
    lost_card_sessions: int
    active_sessions: int
    computed_total_piastres: int
    closing_cash_piastres: int | None
    discrepancy_piastres: int | None

__all__ = [
    "ShiftOpenRequest",
    "ShiftCloseRequest",
    "ShiftResponse",
    "ShiftSummaryResponse",
]
