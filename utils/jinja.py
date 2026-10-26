import json
import os
from datetime import datetime, date
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo
from starlette.templating import Jinja2Templates

from utils.time import cairo_date_str, utc_to_cairo

CAIRO_TZ = ZoneInfo("Africa/Cairo")

# Load translations
_translations = {}
_trans_path = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "translations", "ar.json"
)
if os.path.exists(_trans_path):
    with open(_trans_path, "r", encoding="utf-8") as f:
        _translations = json.load(f)


def t(key: str, default: str | None = None) -> str:
    """Translate key using ar.json, returning key if not found."""
    return _translations.get(key, default if default is not None else key)


def cairo_now() -> datetime:
    """Return the current datetime localized to Africa/Cairo."""
    return datetime.now(CAIRO_TZ)


def discrepancy_class_filter(
    discrepancy_piastres: int | None,
    computed_total: int = 0,
) -> str:
    """Return Tailwind text color class based on discrepancy."""
    if discrepancy_piastres is None:
        return "text-gray-400"
    if discrepancy_piastres == 0:
        return "text-green-600"
    if abs(discrepancy_piastres) / max(computed_total, 1) <= 0.05:
        return "text-amber-500"
    return "text-red-600"


def format_cairo_12h(dt: datetime | date | None, fmt: str | None = None) -> str:
    """Format datetime into 12-hour Cairo local datetime string with Arabic AM/PM (ص / م).
    Default format: 'YYYY/MM/DD • hh:mm ص/م'.
    """
    if dt is None:
        return "—"
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return dt.strftime(fmt or "%Y/%m/%d")

    c_dt = utc_to_cairo(dt)
    if fmt is not None:
        formatted = c_dt.strftime(fmt)
        return formatted.replace("AM", "ص").replace("PM", "م").replace("am", "ص").replace("pm", "م")

    time_part = c_dt.strftime("%I:%M")
    ampm = "م" if c_dt.hour >= 12 else "ص"
    return f"\u200e{c_dt.strftime('%Y/%m/%d')} \u2022 \u200e{time_part} {ampm}"


def cairo_time_filter(dt: datetime | None) -> str:
    """Format time only in 12-hour Cairo local format: 'hh:mm م/ص'."""
    if dt is None:
        return "—"
    c_dt = utc_to_cairo(dt)
    time_part = c_dt.strftime("%I:%M")
    ampm = "م" if c_dt.hour >= 12 else "ص"
    return f"\u200e{time_part} {ampm}"


def cairo_date_filter(dt: datetime | date | None, fmt: str = "%Y-%m-%d") -> str:
    """Format a UTC or naive datetime into Cairo local date string."""
    if dt is None:
        return "—"
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return dt.strftime(fmt)
    if fmt == "%Y-%m-%d":
        return cairo_date_str(dt)
    return format_cairo_12h(dt, fmt=fmt)


def session_status_label_filter(status: str | Any) -> str:
    """Return Arabic label for session status."""
    val = status.value if hasattr(status, "value") else status
    labels = {
        "ACTIVE": "داخل",
        "COMPLETED": "خرج",
        "LOST_CARD": "كرت مفقود",
    }
    return labels.get(str(val), str(status) if status is not None else "")


def shift_status_label_filter(ended_at: datetime | None) -> str:
    """Return 'مفتوح' if ended_at is None, else 'مغلق'."""
    return "مفتوح" if ended_at is None else "مغلق"


def cairo_datetime_filter(dt: datetime | None, fmt: str | None = None) -> str:
    """Format a UTC or naive datetime into full Cairo local datetime string in 12-hour format."""
    if dt is None:
        return "—"
    return format_cairo_12h(dt, fmt=fmt)


def piastres_to_egp_filter(piastres: int | None) -> str:
    """Convert integer piastres to EGP currency format string (e.g. 10000 -> 100) without decimals or piastres."""
    if piastres is None:
        return "0"
    return str(int(round(Decimal(piastres) / Decimal(100))))


def duration_ar_filter(duration_minutes: int | float | None) -> str:
    """Convert minutes into Arabic duration string."""
    if duration_minutes is None:
        return "—"
    total = int(duration_minutes)
    hours = total // 60
    mins = total % 60
    parts = []
    if hours > 0:
        parts.append(f"{hours} س")
    if mins > 0 or hours == 0:
        parts.append(f"{mins} د")
    return " ".join(parts)


def days_remaining_filter(val: date | datetime | int | None) -> int:
    """Calculate remaining days until end_date relative to Cairo today,
    or pass-through if int.
    """
    if val is None:
        return 0
    if isinstance(val, int):
        return max(val, 0)
    today = cairo_now().date()
    if isinstance(val, datetime):
        val = val.date()
    return max((val - today).days, 0)


def to_arabic_indic(val: Any) -> str:
    """Convert ASCII digits to Eastern Arabic (Arabic-Indic) digits."""
    if val is None:
        return ""
    mapping = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
    return str(val).translate(mapping)


def zfill_filter(val: Any, width: int = 6) -> str:
    """Pad string or integer with leading zeroes."""
    if val is None:
        return "0".zfill(width)
    return str(val).zfill(width)


def subscription_status_class_filter(status: Any) -> str:
    """Return Tailwind CSS class based on subscription status."""
    if status is None:
        return "bg-gray-100 text-gray-500"
    val = status.value if hasattr(status, "value") else str(status)
    val_upper = str(val).upper()
    classes = {
        "ACTIVE": "bg-green-100 text-green-800",
        "EXPIRED": "bg-gray-100 text-gray-800",
        "CANCELLED": "bg-red-100 text-red-800",
        "PENDING": "bg-amber-100 text-amber-800",
        "SUSPENDED": "bg-yellow-100 text-yellow-800",
    }
    return classes.get(val_upper, "bg-gray-100 text-gray-500")


def subscription_status_label_filter(status: Any) -> str:
    """Translate subscription status to Arabic."""
    if status is None:
        return "—"
    val = status.value if hasattr(status, "value") else str(status)
    val_upper = str(val).upper()
    labels = {
        "ACTIVE": "نشط",
        "EXPIRED": "منتهي",
        "CANCELLED": "ملغي",
        "PENDING": "معلق",
        "SUSPENDED": "موقوف",
    }
    return labels.get(val_upper, str(val))


subscription_status_badge_filter = subscription_status_class_filter
subscription_status_ar_filter = subscription_status_label_filter


def register_jinja_filters(templates: Jinja2Templates) -> None:
    """Register custom filters and globals to Starlette Jinja2Templates instance."""
    templates.env.globals["t"] = t
    templates.env.filters["t"] = t
    templates.env.filters["cairo_date"] = cairo_date_filter
    templates.env.filters["cairo_datetime"] = cairo_datetime_filter
    templates.env.filters["cairo_time"] = cairo_time_filter
    templates.env.filters["format_date"] = cairo_date_filter
    templates.env.filters["format_datetime"] = cairo_datetime_filter
    templates.env.filters["format_time"] = cairo_time_filter
    templates.env.filters["piastres_to_egp"] = piastres_to_egp_filter
    templates.env.filters["format_egp"] = piastres_to_egp_filter
    templates.env.filters["duration_ar"] = duration_ar_filter
    templates.env.filters["format_duration"] = duration_ar_filter
    templates.env.filters["days_remaining"] = days_remaining_filter
    templates.env.filters["to_arabic_indic"] = to_arabic_indic
    templates.env.filters["zfill"] = zfill_filter
    templates.env.filters["subscription_status_class"] = (
        subscription_status_class_filter
    )
    templates.env.filters["subscription_status_badge"] = (
        subscription_status_badge_filter
    )
    templates.env.filters["subscription_status_ar"] = subscription_status_ar_filter
    templates.env.filters["subscription_status_label"] = (
        subscription_status_label_filter
    )
    templates.env.filters["discrepancy_class"] = discrepancy_class_filter
    templates.env.filters["session_status_label"] = session_status_label_filter
    templates.env.filters["shift_status_label"] = shift_status_label_filter


def create_jinja2_environment(settings=None) -> Jinja2Templates:
    """Factory to create Jinja2Templates with all registered filters and globals."""
    base_dir = os.path.dirname(os.path.dirname(__file__))
    templates_dir = os.path.join(base_dir, "templates")
    templates = Jinja2Templates(directory=templates_dir)
    register_jinja_filters(templates)
    return templates

