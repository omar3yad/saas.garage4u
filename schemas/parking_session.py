from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from models.parking_session import SessionStatus, PaymentMethod

# Request Schemas
class SessionOpenRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    card_code: str = Field(min_length=1, max_length=50)
    plate_number: str | None = None

class SessionExitRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class SessionLostCardRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    plate_number: str = Field(min_length=1, max_length=30)
    notes: str | None = None

class SessionAdminOverrideCloseRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    amount_charged_egp: float = Field(0.0, ge=0)
    override_note: str | None = None


# Response Schemas
class PriceBreakdownResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    duration_minutes: int
    billable_minutes: int
    billable_hours: int
    rate_per_hour: int
    grace_period_mins: int
    minimum_charge: int
    base_amount: int
    penalty_amount: int
    total_amount: int
    pricing_rule_id: int
    is_grace_period: bool
    is_lost_card: bool

class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    garage_id: int
    receipt_no: int
    ticket_code: str | None = None
    card_id: int | None = None
    card_code: str | None = None
    status: SessionStatus
    gate_number: int
    shift_id: int
    operator_id: int
    entry_time: datetime
    exit_time: datetime | None
    plate_number: str | None
    duration_minutes: int | None
    pricing_rule_id: int | None
    amount_charged: int | None
    is_lost_card: bool
    lost_card_penalty_applied: int | None
    payment_method: PaymentMethod
    is_paid: bool
    exit_operator_id: int | None
    exit_shift_id: int | None
    receipt_printed_at: datetime | None
    notes: str | None
    created_at: datetime

class SessionLookupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    session: SessionResponse
    price_breakdown: PriceBreakdownResponse

__all__ = [
    "SessionOpenRequest",
    "SessionExitRequest",
    "SessionLostCardRequest",
    "PriceBreakdownResponse",
    "SessionResponse",
    "SessionLookupResponse",
]
