from collections.abc import AsyncIterator
from typing import Any
import asyncio
import csv
import io

from utils.time import cairo_date_str_full

CSV_BOM = "\ufeff"

STATUS_LABELS: dict[str, str] = {
    "ACTIVE": "داخل",
    "COMPLETED": "خرج",
    "LOST_CARD": "كرت مفقود",
}

PAYMENT_METHOD_LABELS: dict[str, str] = {
    "CASH": "كاش",
    "cash": "كاش",
}


def _piastres_to_egp_str(piastres: int | None) -> str:
    if piastres is None:
        return ""
    # display only — whole EGP, no piastres
    return str(int(round(piastres / 100)))


def _row_to_str(row: list[Any]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(row)
    return output.getvalue()


async def generate_sessions_csv(
    sessions_iter: AsyncIterator[Any],
    operator_names: dict[int, str],
) -> AsyncIterator[str]:
    # 1. Yield CSV BOM
    yield CSV_BOM

    # 2. Yield Arabic header row
    headers = [
        "رقم الجلسة",
        "رمز الكرت",
        "رقم اللوحة",
        "البوابة",
        "عامل الدخول",
        "عامل الخروج",
        "وقت الدخول",
        "وقت الخروج",
        "المدة (دقيقة)",
        "الحالة",
        "المبلغ (جنيه)",
        "كرت مفقود",
        "طريقة الدفع",
    ]
    yield _row_to_str(headers)

    # 3. Async-iterate sessions_iter and yield rows
    count = 0
    async for session in sessions_iter:
        raw_status = (
            session.status.value
            if hasattr(session.status, "value")
            else str(session.status) if session.status is not None else ""
        )
        status_display = STATUS_LABELS.get(raw_status, raw_status)

        raw_pm = (
            session.payment_method.value
            if hasattr(session.payment_method, "value")
            else (
                str(session.payment_method)
                if session.payment_method is not None
                else ""
            )
        )
        pm_display = PAYMENT_METHOD_LABELS.get(raw_pm, raw_pm)

        row = [
            session.id if session.id is not None else "",
            session.card_code if session.card_code is not None else "",
            session.plate_number if session.plate_number is not None else "",
            session.gate_number if session.gate_number is not None else "",
            (
                operator_names.get(session.operator_id, "")
                if session.operator_id is not None
                else ""
            ),
            (
                operator_names.get(getattr(session, "exit_operator_id", None), "")
                if getattr(session, "exit_operator_id", None) is not None
                else ""
            ),
            (
                cairo_date_str_full(session.entry_time)
                if getattr(session, "entry_time", None)
                else ""
            ),
            (
                cairo_date_str_full(session.exit_time)
                if getattr(session, "exit_time", None)
                else ""
            ),
            session.duration_minutes if session.duration_minutes is not None else "",
            status_display,
            _piastres_to_egp_str(session.amount_charged),
            "نعم" if getattr(session, "is_lost_card", False) else "لا",
            pm_display,
        ]
        yield _row_to_str(row)

        count += 1
        if count % 100 == 0:
            await asyncio.sleep(0)


async def generate_shifts_csv(
    shifts_iter: AsyncIterator[Any],
    operator_names: dict[int, str],
    session_totals: dict[int, int],
) -> AsyncIterator[str]:
    # 1. Yield CSV BOM
    yield CSV_BOM

    # 2. Yield Arabic header row
    headers = [
        "رقم الشيفت",
        "العامل",
        "البوابة",
        "بداية الشيفت",
        "نهاية الشيفت",
        "عدد الجلسات",
        "الإجمالي المحسوب (جنيه)",
        "الكاش الختامي (جنيه)",
        "الفرق (جنيه)",
    ]
    yield _row_to_str(headers)

    # 3. Async-iterate shifts_iter and yield rows
    count = 0
    async for shift in shifts_iter:
        session_count = getattr(
            shift, "session_count", getattr(shift, "total_sessions", None)
        )
        session_count_str = str(session_count) if session_count is not None else ""

        computed_total = session_totals.get(shift.id)
        computed_total_str = _piastres_to_egp_str(computed_total)

        closing_cash_str = _piastres_to_egp_str(shift.closing_cash_egp)

        if shift.closing_cash_egp is not None and computed_total is not None:
            discrepancy = shift.closing_cash_egp - computed_total
            discrepancy_str = _piastres_to_egp_str(discrepancy)
        else:
            discrepancy_str = ""

        row = [
            shift.id if shift.id is not None else "",
            (
                operator_names.get(shift.operator_id, "")
                if shift.operator_id is not None
                else ""
            ),
            shift.gate_number if shift.gate_number is not None else "",
            (
                cairo_date_str_full(shift.started_at)
                if getattr(shift, "started_at", None)
                else ""
            ),
            (
                cairo_date_str_full(shift.ended_at)
                if getattr(shift, "ended_at", None)
                else ""
            ),
            session_count_str,
            computed_total_str,
            closing_cash_str,
            discrepancy_str,
        ]
        yield _row_to_str(row)

        count += 1
        if count % 100 == 0:
            await asyncio.sleep(0)


__all__ = ["generate_sessions_csv", "generate_shifts_csv"]
