from datetime import date, datetime
from pydantic import BaseModel


class ReceiptData(BaseModel):
    session_id: int
    card_code: str
    plate_number: str | None
    gate_number: int
    operator_name: str
    entry_time: datetime
    exit_time: datetime
    duration_minutes: int
    duration_display: str
    pricing_rule_label: str
    rate_per_hour: int = 0
    grace_period_mins: int = 0
    base_amount: int = 0
    penalty_amount: int = 0
    total_amount: int = 0
    total_display: str = "٠ ج.م"
    payment_method: str = "نقدي"
    is_lost_card: bool = False
    is_grace_period: bool = False
    company_name: str = "garage4u"
    garage_name: str = "جراج باب اللوق"
    subscriber_name: str | None = None
    subscription_end_date: date | None = None


__all__ = ["ReceiptData"]
