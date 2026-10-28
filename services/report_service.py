from datetime import date, datetime, time, timedelta, timezone
from typing import Any
import asyncio
import logging
from sqlalchemy.ext.asyncio import AsyncSession

from models.parking_session import ParkingSession
from repositories.report_repo import ReportRepository
from repositories.subscription_repo import SubscriptionRepository
from schemas.admin_reports import (
    LiveStatsResponse,
    GateStatusResponse,
    RevenueSummaryResponse,
    GateRevenueResponse,
    ServiceTypeRevenueResponse,
    OperatorRevenueResponse,
    DailyRevenueResponse,
    ReportFilters,
)
from schemas.subscriptions import SubscriptionRevenueSummary, PlanRevenueResponse
from utils.time import (
    CAIRO_TZ,
    cairo_now,
    cairo_today_start,
    cairo_date_to_utc_start,
    cairo_date_to_utc_end,
)

logger = logging.getLogger(__name__)


class ReportService:
    def __init__(self, db: AsyncSession, report_repo: ReportRepository | None = None):
        self.db = db
        self.report_repo = (
            report_repo if report_repo is not None else ReportRepository(db)
        )

    async def get_live_stats(self) -> LiveStatsResponse:
        """Fetches live dashboard KPIs concurrently with graceful fallback."""
        now_dt = datetime.utcnow()
        results = await asyncio.gather(
            self.report_repo.count_active_sessions(),
            self.report_repo.count_total_card_capacity(),
            self.report_repo.sum_revenue_today(
                cairo_today_start(), cairo_date_to_utc_end(cairo_now().date())
            ),
            self.report_repo.count_open_shifts(),
            self.report_repo.count_long_stay_sessions(now_dt - timedelta(hours=24)),
            return_exceptions=True,
        )

        clean_results: list[int] = []
        for r in results:
            if isinstance(r, BaseException):
                logger.error(f"Error fetching live stats metric: {r}")
                clean_results.append(0)
            else:
                clean_results.append(int(r) if r else 0)

        active, capacity, revenue_today, open_shifts, _ = clean_results
        occupancy_pct = round(active * 100 / max(capacity, 1))

        return LiveStatsResponse(
            active_sessions=active,
            total_capacity=capacity,
            occupancy_pct=occupancy_pct,
            revenue_today_piastres=revenue_today,
            open_shifts=open_shifts,
        )

    async def get_gate_panel(self) -> list[GateStatusResponse]:
        """Returns gate status for gates 1 through 5, padding empty gates."""
        rows = await self.report_repo.get_gate_panel()
        gate_map = {r["gate_number"]: r for r in rows}

        panel: list[GateStatusResponse] = []
        for g in range(1, 6):
            if g in gate_map:
                info = gate_map[g]
                panel.append(
                    GateStatusResponse(
                        gate_number=g,
                        operator_name=info["operator_name"],
                        operator_id=info["operator_id"],
                        shift_start=info["shift_start"],
                        active_sessions=info["active_sessions"],
                    )
                )
            else:
                panel.append(
                    GateStatusResponse(
                        gate_number=g,
                        operator_name=None,
                        operator_id=None,
                        shift_start=None,
                        active_sessions=0,
                    )
                )
        return panel

    async def get_alert_counts(self) -> dict[str, int]:
        """Returns counts for long-stay active sessions and overdue shifts."""
        now_dt = datetime.utcnow()
        results = await asyncio.gather(
            self.report_repo.count_long_stay_sessions(now_dt - timedelta(hours=24)),
            self.report_repo.count_overdue_shifts(now_dt - timedelta(hours=12)),
            return_exceptions=True,
        )

        r0 = results[0]
        r1 = results[1]
        long_stay = 0 if isinstance(r0, BaseException) else int(r0 or 0)
        overdue_shifts = 0 if isinstance(r1, BaseException) else int(r1 or 0)

        return {
            "long_stay": long_stay,
            "overdue_shifts": overdue_shifts,
        }

    async def get_revenue_summary(
        self, filters: ReportFilters
    ) -> RevenueSummaryResponse:
        """Aggregates revenue, session count, and averages over filtered period."""
        start_utc = (
            cairo_date_to_utc_start(filters.start_date) if filters.start_date else None
        )
        end_utc = cairo_date_to_utc_end(filters.end_date) if filters.end_date else None

        res = await self.report_repo.get_revenue_summary(
            start_utc=start_utc,
            end_utc=end_utc,
            gate_number=filters.gate_number,
            operator_id=filters.operator_id,
        )

        total_sessions = res["total_sessions"]
        total_revenue = res["total_revenue"]
        total_duration = res["total_duration"]

        avg_duration = total_duration // max(total_sessions, 1)
        avg_revenue = total_revenue // max(total_sessions, 1)

        return RevenueSummaryResponse(
            total_sessions=total_sessions,
            total_revenue_piastres=total_revenue,
            avg_duration_minutes=avg_duration,
            avg_revenue_piastres=avg_revenue,
        )

    async def get_revenue_by_gate(
        self, filters: ReportFilters
    ) -> list[GateRevenueResponse]:
        """Returns revenue breakdown per gate."""
        start_utc = (
            cairo_date_to_utc_start(filters.start_date) if filters.start_date else None
        )
        end_utc = cairo_date_to_utc_end(filters.end_date) if filters.end_date else None

        rows = await self.report_repo.get_revenue_by_gate(
            start_utc=start_utc,
            end_utc=end_utc,
            operator_id=filters.operator_id,
        )
        return [GateRevenueResponse(**r) for r in rows]

    async def get_revenue_by_service_type(
        self, filters: ReportFilters
    ) -> list[ServiceTypeRevenueResponse]:
        """Returns revenue breakdown per service type (Tickets, Subscriptions, Lost Cards)."""
        start_utc = (
            cairo_date_to_utc_start(filters.start_date) if filters.start_date else None
        )
        end_utc = cairo_date_to_utc_end(filters.end_date) if filters.end_date else None

        rows = await self.report_repo.get_revenue_by_service_type(
            start_utc=start_utc,
            end_utc=end_utc,
            operator_id=filters.operator_id,
        )
        return [ServiceTypeRevenueResponse(**r) for r in rows]

    async def get_revenue_by_operator(
        self, filters: ReportFilters
    ) -> list[OperatorRevenueResponse]:
        """Returns revenue breakdown per operator."""
        start_utc = (
            cairo_date_to_utc_start(filters.start_date) if filters.start_date else None
        )
        end_utc = cairo_date_to_utc_end(filters.end_date) if filters.end_date else None

        rows = await self.report_repo.get_revenue_by_operator(
            start_utc=start_utc,
            end_utc=end_utc,
            gate_number=filters.gate_number,
        )
        return [OperatorRevenueResponse(**r) for r in rows]

    async def get_daily_revenue(
        self, filters: ReportFilters
    ) -> list[DailyRevenueResponse]:
        """Returns daily revenue breakdown across date range, zero-filling
        missing calendar days.
        """
        today_cairo = cairo_now().date()
        start_d = filters.start_date or today_cairo
        end_d = filters.end_date or today_cairo

        start_utc = cairo_date_to_utc_start(start_d)
        end_utc = cairo_date_to_utc_end(end_d)

        rows = await self.report_repo.get_daily_revenue_raw(
            start_utc=start_utc,
            end_utc=end_utc,
            gate_number=filters.gate_number,
            operator_id=filters.operator_id,
        )
        row_map = {r["cairo_date"]: r for r in rows}

        results: list[DailyRevenueResponse] = []
        curr = start_d
        while curr <= end_d:
            d_str = curr.strftime("%Y-%m-%d")
            if d_str in row_map:
                results.append(
                    DailyRevenueResponse(
                        date_str=d_str,
                        session_count=row_map[d_str]["session_count"],
                        total_piastres=row_map[d_str]["total_piastres"],
                    )
                )
            else:
                results.append(
                    DailyRevenueResponse(
                        date_str=d_str,
                        session_count=0,
                        total_piastres=0,
                    )
                )
            curr += timedelta(days=1)

        return results

    async def get_monthly_revenue(
        self, filters: ReportFilters
    ) -> list[dict[str, Any]]:
        """Returns monthly revenue breakdown."""
        start_utc = (
            cairo_date_to_utc_start(filters.start_date) if filters.start_date else None
        )
        end_utc = cairo_date_to_utc_end(filters.end_date) if filters.end_date else None

        rows = await self.report_repo.get_monthly_revenue_raw(
            start_utc=start_utc,
            end_utc=end_utc,
            gate_number=filters.gate_number,
            operator_id=filters.operator_id,
        )

        if filters.start_date and filters.end_date:
            curr_y = filters.start_date.year
            curr_m = filters.start_date.month
            end_y = filters.end_date.year
            end_m = filters.end_date.month

            existing_map = {r["month_str"]: r for r in rows}
            results = []
            while (curr_y < end_y) or (curr_y == end_y and curr_m <= end_m):
                m_str = f"{curr_y:04d}-{curr_m:02d}"
                if m_str in existing_map:
                    results.append(existing_map[m_str])
                else:
                    results.append({
                        "month_str": m_str,
                        "session_count": 0,
                        "ticket_piastres": 0,
                        "sub_count": 0,
                        "sub_piastres": 0,
                        "total_piastres": 0,
                    })
                curr_m += 1
                if curr_m > 12:
                    curr_m = 1
                    curr_y += 1
            return results

        return rows

    async def get_sessions_filtered(
        self,
        filters: ReportFilters,
        page: int,
        size: int,
    ) -> tuple[list[ParkingSession], int]:
        """Delegates paginated session querying to repository."""
        return await self.report_repo.get_sessions_filtered(filters, page, size)

    async def get_subscription_revenue_summary(
        self,
        start_date: date | None,
        end_date: date | None,
        plan_id: int | None,
        subscription_repo: SubscriptionRepository,
    ) -> SubscriptionRevenueSummary:
        """Computes aggregated subscription financial metrics over a Cairo
        date range.
        """
        start_utc: datetime | None = None
        end_utc: datetime | None = None

        if start_date is not None:
            cairo_start = datetime.combine(start_date, time.min).replace(
                tzinfo=CAIRO_TZ
            )
            start_utc = cairo_start.astimezone(timezone.utc).replace(tzinfo=None)

        if end_date is not None:
            cairo_end = datetime.combine(end_date, time.max).replace(tzinfo=CAIRO_TZ)
            end_utc = cairo_end.astimezone(timezone.utc).replace(tzinfo=None)

        rows = await subscription_repo.get_revenue_by_plan(start_utc, end_utc, plan_id)

        by_plan = [
            PlanRevenueResponse(
                plan_id=row["plan_id"],
                plan_label=row["plan_label"],
                subscription_count=row["subscription_count"],
                total_piastres=row["total_piastres"],
            )
            for row in rows
        ]

        total_subscriptions = sum(p.subscription_count for p in by_plan)
        total_revenue_piastres = sum(p.total_piastres for p in by_plan)
        avg_revenue_piastres = (
            total_revenue_piastres // max(total_subscriptions, 1)
            if total_subscriptions > 0
            else 0
        )

        return SubscriptionRevenueSummary(
            total_subscriptions=total_subscriptions,
            total_revenue_piastres=total_revenue_piastres,
            avg_revenue_piastres=avg_revenue_piastres,
            by_plan=by_plan,
        )


__all__ = ["ReportService"]
