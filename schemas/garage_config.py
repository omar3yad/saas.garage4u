from datetime import datetime
from pydantic import BaseModel, Field, model_validator, ConfigDict

class ReceiptSettingsSchema(BaseModel):
    header_title: str = Field(default="جراج سيارات", max_length=100)
    header_subtitle: str = Field(default="أهلاً بكم", max_length=100)
    footer_terms: str = Field(default="الجراج غير مسؤول عن محتويات السيارات الداخلية", max_length=255)
    show_qr_code: bool = True

class GarageConfigBase(BaseModel):
    ticket_system: str = Field(default="REUSABLE_CARDS", pattern=r"^(REUSABLE_CARDS|PAPER_BARCODE_TICKETS)$")
    anpr_enabled: bool = False
    exit_photo_enabled: bool = False
    subscriptions_enabled: bool = True
    require_shift_cash_reconciliation: bool = True
    min_session_duration_seconds: int = Field(default=60, ge=30, le=600)
    receipt_settings: ReceiptSettingsSchema = Field(default_factory=ReceiptSettingsSchema)

    @model_validator(mode="after")
    def validate_anpr_exit_photo(self) -> "GarageConfigBase":
        if self.exit_photo_enabled and not self.anpr_enabled:
            raise ValueError("exit_photo_enabled requires anpr_enabled to be True")
        return self

class GarageConfigUpdate(BaseModel):
    ticket_system: str | None = Field(default=None, pattern=r"^(REUSABLE_CARDS|PAPER_BARCODE_TICKETS)$")
    anpr_enabled: bool | None = None
    exit_photo_enabled: bool | None = None
    subscriptions_enabled: bool | None = None
    require_shift_cash_reconciliation: bool | None = None
    min_session_duration_seconds: int | None = Field(default=None, ge=30, le=600)
    receipt_settings: ReceiptSettingsSchema | None = None

    @model_validator(mode="after")
    def validate_anpr_exit_photo_update(self) -> "GarageConfigUpdate":
        if self.exit_photo_enabled is True and self.anpr_enabled is False:
            raise ValueError("exit_photo_enabled requires anpr_enabled to be True")
        return self

class GarageConfigResponse(GarageConfigBase):
    id: int
    garage_id: int
    version: int
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

__all__ = [
    "ReceiptSettingsSchema",
    "GarageConfigBase",
    "GarageConfigUpdate",
    "GarageConfigResponse",
]
