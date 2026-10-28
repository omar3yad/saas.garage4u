from collections.abc import AsyncIterator
from datetime import date
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
    # display only — not stored
    return f"{piastres / 100:.2f}"


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


ARABIC_MONTH_NAMES: dict[str, str] = {
    "01": "يناير",
    "02": "فبراير",
    "03": "مارس",
    "04": "أبريل",
    "05": "مايو",
    "06": "يونيو",
    "07": "يوليو",
    "08": "أغسطس",
    "09": "سبتمبر",
    "10": "أكتوبر",
    "11": "نوفمبر",
    "12": "ديسمبر",
}

ARABIC_DAY_NAMES: dict[int, str] = {
    0: "الإثنين",
    1: "الثلاثاء",
    2: "الأربعاء",
    3: "الخميس",
    4: "الجمعة",
    5: "السبت",
    6: "الأحد",
}


async def generate_monthly_revenue_csv(
    monthly_data: list[dict[str, Any]],
) -> AsyncIterator[str]:
    # 1. Yield CSV BOM
    yield CSV_BOM

    # 2. Yield Arabic header row
    headers = [
        "الشهر",
        "اسم الشهر",
        "عدد تذاكر الانتظار",
        "إيراد الانتظار (جنيه)",
        "عدد الاشتراكات",
        "إيراد الاشتراكات (جنيه)",
        "إجمالي الإيراد (جنيه)",
        "متوسط إيراد التذكرة (جنيه)",
    ]
    yield _row_to_str(headers)

    grand_sessions = 0
    grand_tickets_piastres = 0
    grand_subs = 0
    grand_subs_piastres = 0
    grand_total_piastres = 0

    for item in monthly_data:
        m_str = str(item.get("month_str", ""))
        month_name = ""
        if "-" in m_str:
            parts = m_str.split("-")
            if len(parts) == 2:
                y, m = parts[0], parts[1]
                ar_m = ARABIC_MONTH_NAMES.get(m, m)
                month_name = f"{ar_m} {y}"

        s_cnt = int(item.get("session_count", 0) or 0)
        t_piastres = int(item.get("ticket_piastres", 0) or 0)
        sub_cnt = int(item.get("sub_count", 0) or 0)
        sub_piastres = int(item.get("sub_piastres", 0) or 0)
        tot_piastres = int(item.get("total_piastres", t_piastres + sub_piastres) or 0)

        grand_sessions += s_cnt
        grand_tickets_piastres += t_piastres
        grand_subs += sub_cnt
        grand_subs_piastres += sub_piastres
        grand_total_piastres += tot_piastres

        avg_ticket_str = (
            _piastres_to_egp_str(t_piastres // s_cnt) if s_cnt > 0 else "0.00"
        )

        row = [
            m_str,
            month_name,
            s_cnt,
            _piastres_to_egp_str(t_piastres),
            sub_cnt,
            _piastres_to_egp_str(sub_piastres),
            _piastres_to_egp_str(tot_piastres),
            avg_ticket_str,
        ]
        yield _row_to_str(row)
        await asyncio.sleep(0)

    # Summary row at the bottom
    grand_avg_ticket_str = (
        _piastres_to_egp_str(grand_tickets_piastres // grand_sessions)
        if grand_sessions > 0
        else "0.00"
    )
    summary_row = [
        "الإجمالي العام",
        "",
        grand_sessions,
        _piastres_to_egp_str(grand_tickets_piastres),
        grand_subs,
        _piastres_to_egp_str(grand_subs_piastres),
        _piastres_to_egp_str(grand_total_piastres),
        grand_avg_ticket_str,
    ]
    yield _row_to_str(summary_row)


async def generate_daily_revenue_csv(
    daily_data: list[Any],
) -> AsyncIterator[str]:
    # 1. Yield CSV BOM
    yield CSV_BOM

    # 2. Yield Arabic header row
    headers = [
        "التاريخ",
        "اليوم",
        "عدد العمليات / الجلسات",
        "إجمالي الإيراد (جنيه)",
        "متوسط قيمة العملية (جنيه)",
    ]
    yield _row_to_str(headers)

    grand_sessions = 0
    grand_piastres = 0

    for d in daily_data:
        date_str = str(getattr(d, "date_str", None) or (d.get("date_str") if isinstance(d, dict) else "") or "")
        session_count = int(getattr(d, "session_count", None) if hasattr(d, "session_count") else (d.get("session_count", 0) if isinstance(d, dict) else 0) or 0)
        total_piastres = int(getattr(d, "total_piastres", None) if hasattr(d, "total_piastres") else (d.get("total_piastres", 0) if isinstance(d, dict) else 0) or 0)

        day_name = ""
        try:
            dt = date.fromisoformat(date_str)
            day_name = ARABIC_DAY_NAMES.get(dt.weekday(), "")
        except Exception:
            pass

        grand_sessions += session_count
        grand_piastres += total_piastres

        avg_str = (
            _piastres_to_egp_str(total_piastres // session_count)
            if session_count > 0
            else "0.00"
        )

        row = [
            date_str,
            day_name,
            session_count,
            _piastres_to_egp_str(total_piastres),
            avg_str,
        ]
        yield _row_to_str(row)
        await asyncio.sleep(0)

    grand_avg_str = (
        _piastres_to_egp_str(grand_piastres // grand_sessions)
        if grand_sessions > 0
        else "0.00"
    )
    summary_row = [
        "الإجمالي العام",
        "",
        grand_sessions,
        _piastres_to_egp_str(grand_piastres),
        grand_avg_str,
    ]
    yield _row_to_str(summary_row)


async def generate_operator_revenue_csv(
    operator_data: list[Any],
) -> AsyncIterator[str]:
    # 1. Yield CSV BOM
    yield CSV_BOM

    # 2. Yield Arabic header row
    headers = [
        "اسم العامل",
        "معرف العامل",
        "عدد العمليات / الجلسات",
        "إجمالي الإيراد (جنيه)",
        "متوسط قيمة العملية (جنيه)",
        "نسبة التحصيل (٪)",
    ]
    yield _row_to_str(headers)

    grand_sessions = 0
    grand_piastres = 0

    for op in operator_data:
        s_cnt = int(getattr(op, "session_count", None) if hasattr(op, "session_count") else (op.get("session_count", 0) if isinstance(op, dict) else 0) or 0)
        t_piastres = int(getattr(op, "total_piastres", None) if hasattr(op, "total_piastres") else (op.get("total_piastres", 0) if isinstance(op, dict) else 0) or 0)
        grand_sessions += s_cnt
        grand_piastres += t_piastres

    for op in operator_data:
        op_name = str(getattr(op, "operator_name", None) or (op.get("operator_name") if isinstance(op, dict) else "") or "")
        op_id = getattr(op, "operator_id", None) if hasattr(op, "operator_id") else (op.get("operator_id") if isinstance(op, dict) else "")
        s_cnt = int(getattr(op, "session_count", None) if hasattr(op, "session_count") else (op.get("session_count", 0) if isinstance(op, dict) else 0) or 0)
        t_piastres = int(getattr(op, "total_piastres", None) if hasattr(op, "total_piastres") else (op.get("total_piastres", 0) if isinstance(op, dict) else 0) or 0)

        avg_str = (
            _piastres_to_egp_str(t_piastres // s_cnt)
            if s_cnt > 0
            else "0.00"
        )
        pct = (t_piastres / grand_piastres * 100) if grand_piastres > 0 else 0.0

        row = [
            op_name,
            op_id if op_id is not None else "",
            s_cnt,
            _piastres_to_egp_str(t_piastres),
            avg_str,
            f"{pct:.1f}%",
        ]
        yield _row_to_str(row)
        await asyncio.sleep(0)

    grand_avg_str = (
        _piastres_to_egp_str(grand_piastres // grand_sessions)
        if grand_sessions > 0
        else "0.00"
    )
    summary_row = [
        "الإجمالي العام",
        "",
        grand_sessions,
        _piastres_to_egp_str(grand_piastres),
        grand_avg_str,
        "100%" if grand_piastres > 0 else "0%",
    ]
    yield _row_to_str(summary_row)


__all__ = [
    "generate_sessions_csv",
    "generate_shifts_csv",
    "generate_monthly_revenue_csv",
    "generate_daily_revenue_csv",
    "generate_operator_revenue_csv",
]
