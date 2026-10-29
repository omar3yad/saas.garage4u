from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from models import SessionStatus, PaymentMethod
from schemas.audit_log import AuditLogResponse


class LiveStatsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    active_sessions: int
    total_capacity: int
    occupancy_pct: int
    revenue_today_piastres: int
    open_shifts: int


class GateStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    gate_number: int
    operator_name: str | None
    operator_id: int | None
    shift_start: datetime | None
    active_sessions: int


class RevenueSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    total_sessions: int
    total_revenue_piastres: int
    avg_duration_minutes: int
    avg_revenue_piastres: int


class GateRevenueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    gate_number: int
    session_count: int
    total_piastres: int


class ServiceTypeRevenueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    service_type: str
    label: str
    session_count: int
    total_piastres: int
    percentage: float = 0.0


class OperatorRevenueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    operator_id: int
    operator_name: str
    session_count: int
    total_piastres: int


class DailyRevenueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    date_str: str  # "YYYY-MM-DD" Cairo local
    session_count: int
    total_piastres: int


class ReportFilters(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    start_date: date | None = None
    end_date: date | None = None
    gate_number: int | None = Field(None, ge=1, le=10)
    operator_id: int | None = None
    status: SessionStatus | None = None
    card_code: str | None = Field(None, max_length=50)
    plate_number: str | None = Field(None, max_length=30)
    long_stay: bool = False

    @model_validator(mode="after")
    def validate_date_range(self) -> "ReportFilters":
        if self.start_date and self.end_date:
            if self.start_date > self.end_date:
                raise ValueError("start_date must be <= end_date")
        return self


class ShiftFilters(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    operator_id: int | None = None
    gate_number: int | None = Field(None, ge=1, le=10)
    status: Literal["open", "closed"] | None = None
    start_date: date | None = None
    end_date: date | None = None
    overdue: bool = False

    @model_validator(mode="after")
    def validate_date_range(self) -> "ShiftFilters":
        if self.start_date and self.end_date:
            if self.start_date > self.end_date:
                raise ValueError("start_date must be <= end_date")
        return self


class ForceCloseShiftRequest(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    closing_cash_egp: int | None = Field(None, ge=0)
    admin_note: str | None = Field(None, max_length=500)


class AdminSessionDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    card_id: int
    card_code: str
    status: SessionStatus
    gate_number: int
    shift_id: int
    operator_id: int
    entry_time: datetime
    exit_time: datetime | None = None
    plate_number: str | None = None
    duration_minutes: int | None = None
    pricing_rule_id: int | None = None
    amount_charged: int | None = None
    is_lost_card: bool = False
    lost_card_penalty_applied: int | None = None
    payment_method: PaymentMethod = PaymentMethod.CASH
    is_paid: bool = False
    exit_operator_id: int | None = None
    exit_shift_id: int | None = None
    receipt_printed_at: datetime | None = None
    admin_override_by: int | None = None
    admin_override_note: str | None = None
    notes: str | None = None
    created_at: datetime
    audit_logs: list[AuditLogResponse] = []


__all__ = [
    "LiveStatsResponse",
    "GateStatusResponse",
    "RevenueSummaryResponse",
    "GateRevenueResponse",
    "OperatorRevenueResponse",
    "DailyRevenueResponse",
    "ReportFilters",
    "ShiftFilters",
    "ForceCloseShiftRequest",
    "AdminSessionDetail",
]
