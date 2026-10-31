class CardNotFoundError(Exception):
    def __init__(self, message: str = "Card not found"):
        self.message = message
        super().__init__(message)

class CardNotAvailableError(Exception):
    def __init__(self, message: str = "Card not available"):
        self.message = message
        super().__init__(message)

class CardAlreadyActiveError(Exception):
    def __init__(self, message: str = "Card already active"):
        self.message = message
        super().__init__(message)

class CardHasNoActiveSessionError(Exception):
    def __init__(self, message: str = "Card has no active session"):
        self.message = message
        super().__init__(message)

class InvalidBarcodeFormatError(Exception):
    def __init__(self, message: str = "Invalid barcode format"):
        self.message = message
        super().__init__(message)

class BulkCardConflictError(Exception):
    def __init__(self, conflicting_codes: list[str], message: str = "Bulk card creation conflicts"):
        self.conflicting_codes = conflicting_codes
        self.message = message
        super().__init__(message)

class SessionNotActiveError(Exception):
    def __init__(self, message: str = "Session is not active"):
        self.message = message
        super().__init__(message)

class SessionNotFoundError(Exception):
    def __init__(self, message: str = "Session not found"):
        self.message = message
        super().__init__(message)

class ShiftAlreadyOpenError(Exception):
    def __init__(self, message: str = "Shift is already open"):
        self.message = message
        super().__init__(message)

class NoActiveShiftError(Exception):
    def __init__(self, message: str = "No active shift"):
        self.message = message
        super().__init__(message)

class ShiftNotFoundError(Exception):
    def __init__(self, message: str = "Shift not found"):
        self.message = message
        super().__init__(message)

class ShiftNotOwnedError(Exception):
    def __init__(self, message: str = "Shift not owned by this operator"):
        self.message = message
        super().__init__(message)

class NoPricingRuleError(Exception):
    def __init__(self, message: str = "No active pricing rule found"):
        self.message = message
        super().__init__(message)

# Phase 4 — Subscription Exceptions
class PlanNotFoundError(Exception):
    def __init__(self, message: str = "Subscription plan not found"):
        self.message = message
        super().__init__(message)

class PlanNotActiveError(Exception):
    def __init__(self, message: str = "Subscription plan is not active"):
        self.message = message
        super().__init__(message)

class PlanLabelAlreadyExistsError(Exception):
    def __init__(self, message: str = "Subscription plan label already exists"):
        self.message = message
        super().__init__(message)

class SubscriberNotFoundError(Exception):
    def __init__(self, message: str = "Subscriber not found"):
        self.message = message
        super().__init__(message)

class SubscriberPlateAlreadyExistsError(Exception):
    def __init__(self, message: str = "Subscriber with this plate number already exists"):
        self.message = message
        super().__init__(message)

class SubscriptionNotFoundError(Exception):
    def __init__(self, message: str = "Subscription not found"):
        self.message = message
        super().__init__(message)

class SubscriptionNotActiveError(Exception):
    def __init__(self, message: str = "Subscription is not active"):
        self.message = message
        super().__init__(message)

class SubscriberAlreadyHasActiveSubscriptionError(Exception):
    def __init__(self, message: str = "Subscriber already has an active or pending subscription"):
        self.message = message
        super().__init__(message)

class SubscriptionDailyLimitReachedError(Exception):
    def __init__(self, message: str = "Daily entry limit reached for this subscription"):
        self.message = message
        super().__init__(message)

class ShiftAlreadyClosedError(Exception):
    def __init__(self, message: str = "Shift is already closed"):
        self.message = message
        super().__init__(message)

class PricingRuleNotFoundError(Exception):
    def __init__(self, message: str = "Pricing rule not found"):
        self.message = message
        super().__init__(message)

class RateLabelAlreadyExistsError(Exception):
    def __init__(self, message: str = "Pricing rule label already exists"):
        self.message = message
        super().__init__(message)

class InvalidDateRangeError(Exception):
    def __init__(self, message: str = "Invalid date range: start_date must be <= end_date"):
        self.message = message
        super().__init__(message)

class InvalidReportTypeError(Exception):
    def __init__(self, message: str = "Invalid report type"):
        self.message = message
        super().__init__(message)

class ShiftIdRequiredForPrintError(Exception):
    def __init__(self, message: str = "shift_id is required for shift print view"):
        self.message = message
        super().__init__(message)

# Phase 5 — Multi-Tenant SaaS Exceptions
class TenantNotFoundError(Exception):
    def __init__(self, message: str = "Tenant or garage not found"):
        self.message = message
        super().__init__(message)

class TenantSuspendedError(Exception):
    def __init__(self, message: str = "Tenant is suspended (Exit-Only mode)"):
        self.message = message
        super().__init__(message)

class BillingPastDueError(Exception):
    def __init__(self, message: str = "Billing subscription is past due (Exit-Only mode)"):
        self.message = message
        super().__init__(message)

class TenantTerminatedError(Exception):
    def __init__(self, message: str = "Tenant operations have been terminated"):
        self.message = message
        super().__init__(message)

class FeatureNotEntitledError(Exception):
    def __init__(self, message: str = "Feature not entitled for this garage"):
        self.message = message
        super().__init__(message)

class ConfigLockedActiveSessionsError(Exception):
    def __init__(self, message: str = "Cannot change configuration while active sessions exist"):
        self.message = message
        super().__init__(message)

class ConfigVersionConflictError(Exception):
    def __init__(self, message: str = "Configuration version conflict"):
        self.message = message
        super().__init__(message)

class ShortSessionConfirmationRequiredError(Exception):
    def __init__(self, elapsed_seconds: int, min_seconds: int, message: str | None = None):
        self.elapsed_seconds = elapsed_seconds
        self.min_seconds = min_seconds
        msg = message or f"Session started only {elapsed_seconds}s ago (min: {min_seconds}s). Confirmation required."
        self.message = msg
        super().__init__(msg)

class RapidDoubleScanError(Exception):
    def __init__(self, message: str = "Rapid double scan detected. Please wait before retrying."):
        self.message = message
        super().__init__(message)

class ExitPhotoRequiresANPRError(Exception):
    def __init__(self, message: str = "Exit photo requires ANPR to be enabled"):
        self.message = message
        super().__init__(message)

class FileTooLargeError(Exception):
    def __init__(self, size: int, max_bytes: int = 1_500_000):
        self.size = size
        self.max_bytes = max_bytes
        super().__init__(f"File size {size} exceeds maximum permitted limit {max_bytes} bytes")

class EdgeAuthFailedError(Exception):
    def __init__(self, message: str = "Edge device authentication failed"):
        self.message = message
        super().__init__(message)

__all__ = [
    "CardNotFoundError",
    "CardNotAvailableError",
    "CardAlreadyActiveError",
    "CardHasNoActiveSessionError",
    "InvalidBarcodeFormatError",
    "BulkCardConflictError",
    "SessionNotActiveError",
    "SessionNotFoundError",
    "ShiftAlreadyOpenError",
    "NoActiveShiftError",
    "ShiftNotFoundError",
    "ShiftNotOwnedError",
    "NoPricingRuleError",
    "PlanNotFoundError",
    "PlanNotActiveError",
    "PlanLabelAlreadyExistsError",
    "SubscriberNotFoundError",
    "SubscriberPlateAlreadyExistsError",
    "SubscriptionNotFoundError",
    "SubscriptionNotActiveError",
    "SubscriberAlreadyHasActiveSubscriptionError",
    "SubscriptionDailyLimitReachedError",
    "ShiftAlreadyClosedError",
    "PricingRuleNotFoundError",
    "RateLabelAlreadyExistsError",
    "InvalidDateRangeError",
    "InvalidReportTypeError",
    "ShiftIdRequiredForPrintError",
    "TenantNotFoundError",
    "TenantSuspendedError",
    "BillingPastDueError",
    "TenantTerminatedError",
    "FeatureNotEntitledError",
    "ConfigLockedActiveSessionsError",
    "ConfigVersionConflictError",
    "ShortSessionConfirmationRequiredError",
    "RapidDoubleScanError",
    "ExitPhotoRequiresANPRError",
    "FileTooLargeError",
    "EdgeAuthFailedError",
]
