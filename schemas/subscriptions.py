from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field, computed_field
from models.subscription import SubscriptionStatus


# ==========================================
# Subscription Plan Schemas
# ==========================================

class PlanCreate(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    duration_days: int = Field(ge=1)
    price_egp: float = Field(ge=0)
    max_entries_per_day: int | None = Field(None, ge=1)
    description: str | None = None

    @property
    def price_piastres(self) -> int:
        return int(round(self.price_egp)) * 100


class PlanUpdate(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=100)
    price_egp: float | None = Field(None, ge=0)
    max_entries_per_day: int | None = Field(None, ge=1)
    description: str | None = None
    is_active: bool | None = None

    @property
    def price_piastres(self) -> int | None:
        if self.price_egp is not None:
            return int(round(self.price_egp)) * 100
        return None


class PlanResponse(BaseModel):
    id: int
    label: str
    duration_days: int
    price_piastres: int
    max_entries_per_day: int | None
    description: str | None
    is_active: bool
    created_by: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @computed_field  # type: ignore[misc]
    @property
    def price_egp(self) -> float:
        return self.price_piastres / 100.0


# ==========================================
# Subscription Schemas
# ==========================================

class SubscriptionResponse(BaseModel):
    id: int
    subscriber_id: int
    plan_id: int
    card_id: int
    plate_number: str
    start_date: date
    end_date: date
    status: SubscriptionStatus
    amount_paid_piastres: int
    plan_price_snapshot: int
    paid_at: datetime | None
    collected_by: int | None
    renewal_count: int
    previous_subscription_id: int | None
    cancel_reason: str | None
    ended_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    plan_label: str | None = None
    days_remaining: int | None = None

    model_config = ConfigDict(from_attributes=True)

    @computed_field  # type: ignore[misc]
    @property
    def amount_paid_egp(self) -> float:
        return self.amount_paid_piastres / 100.0

    @computed_field  # type: ignore[misc]
    @property
    def plan_price_snapshot_egp(self) -> float:
        return self.plan_price_snapshot / 100.0


class SubscriptionCreate(BaseModel):
    subscriber_id: int = Field(gt=0)
    plan_id: int | None = None
    card_id: int | None = None
    card_code: str | None = None
    duration_days: int | None = Field(None, ge=1)
    start_date: date
    end_date: date | None = None
    amount_paid_egp: float = Field(ge=0)
    notes: str | None = None

    @property
    def amount_paid_piastres(self) -> int:
        return int(round(self.amount_paid_egp)) * 100


class SubscriptionRenew(BaseModel):
    plan_id: int | None = Field(None, gt=0)
    start_date: date | None = None
    end_date: date | None = None
    amount_paid_egp: float = Field(ge=0)
    notes: str | None = None

    @property
    def amount_paid_piastres(self) -> int:
        return int(round(self.amount_paid_egp)) * 100



class SubscriptionCancel(BaseModel):
    cancel_reason: str | None = None


# ==========================================
# Subscriber Schemas
# ==========================================

class SubscriberCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    phone_number: str | None = Field(None, max_length=20)
    plate_number: str = Field(min_length=1, max_length=30)
    company_name: str | None = Field(None, max_length=150)
    card_code: str | None = Field(None, max_length=50)
    notes: str | None = None


class SubscriberUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=120)
    phone_number: str | None = Field(None, max_length=20)
    company_name: str | None = Field(None, max_length=150)
    card_code: str | None = Field(None, max_length=50)
    notes: str | None = None


class SubscriberResponse(BaseModel):
    id: int
    full_name: str
    phone_number: str | None
    plate_number: str
    company_name: str | None = None
    card_code: str | None = None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    active_subscription: SubscriptionResponse | None = None

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "PlanCreate",
    "PlanUpdate",
    "PlanResponse",
    "SubscriberCreate",
    "SubscriberUpdate",
    "SubscriberResponse",
    "SubscriptionCreate",
    "SubscriptionRenew",
    "SubscriptionCancel",
    "SubscriptionResponse",
]


# ==========================================
# Reporting & Dashboard Schemas
# ==========================================

class PlanRevenueResponse(BaseModel):
    plan_id: int
    plan_label: str
    subscription_count: int
    total_piastres: int

    model_config = ConfigDict(from_attributes=True)


class SubscriptionRevenueSummary(BaseModel):
    total_subscriptions: int
    total_revenue_piastres: int
    avg_revenue_piastres: int
    by_plan: list[PlanRevenueResponse]

    model_config = ConfigDict(from_attributes=True)


class SubscriptionDashboardStats(BaseModel):
    active_subscriptions_count: int
    expiring_soon_count: int
    expired_unrenewed_count: int
    total_subscribers: int = 0

    @property
    def active_subscriptions(self) -> int:
        return self.active_subscriptions_count

    model_config = ConfigDict(from_attributes=True)

SubscriptionRenewRequest = SubscriptionRenew
SubscriptionCancelRequest = SubscriptionCancel

__all__.extend([
    'SubscriptionRenewRequest',
    'SubscriptionCancelRequest',
    'PlanRevenueResponse',
    'SubscriptionRevenueSummary',
    'SubscriptionDashboardStats',
])
