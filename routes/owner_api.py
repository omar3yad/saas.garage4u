from datetime import datetime, time
from typing import Any
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import require_owner_or_admin
from models.user import User, UserRole
from models.garage import Garage
from models.parking_session import ParkingSession, SessionStatus
from repositories.garage_repo import GarageRepository
from schemas.garage import GarageResponse

router = APIRouter(prefix="/api/v1/owner", tags=["owner"])


@router.get("/garages", response_model=list[GarageResponse])
async def get_owner_garages(
    owner_id_query: int | None = Query(None, alias="owner_id"),
    current_user: User = Depends(require_owner_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Returns the list of garages owned by the authenticated owner.
    Prevents cross-owner leakage by resolving owner_id from the user's verified token.
    """
    garage_repo = GarageRepository(db)

    if current_user.role == UserRole.SUPER_ADMIN:
        target_owner_id = owner_id_query or current_user.owner_id
        if target_owner_id is not None:
            garages = await garage_repo.get_by_owner(target_owner_id)
        else:
            all_garages, _ = await garage_repo.get_all(page=1, size=100)
            return [GarageResponse.model_validate(g) for g in all_garages]
    else:
        if current_user.owner_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not associated with an owner organization",
                headers={"X-Error-Code": "INSUFFICIENT_PERMISSIONS"},
            )
        target_owner_id = current_user.owner_id
        garages = await garage_repo.get_by_owner(target_owner_id)

    return [GarageResponse.model_validate(g) for g in garages]


@router.get("/dashboard")
async def get_owner_fleet_dashboard(
    owner_id_query: int | None = Query(None, alias="owner_id"),
    current_user: User = Depends(require_owner_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Aggregated fleet management dashboard for owner garages.
    Calculates occupancy rates, active sessions, and today's total revenue across all owned facilities.
    """
    garage_repo = GarageRepository(db)

    if current_user.role == UserRole.SUPER_ADMIN and owner_id_query is not None:
        target_owner_id = owner_id_query
    else:
        target_owner_id = current_user.owner_id

    if target_owner_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not associated with an owner organization",
            headers={"X-Error-Code": "INSUFFICIENT_PERMISSIONS"},
        )

    garages = await garage_repo.get_by_owner(target_owner_id)
    if not garages:
        return {
            "owner_id": target_owner_id,
            "total_garages": 0,
            "total_capacity": 0,
            "total_active_sessions": 0,
            "total_today_revenue_egp": 0.0,
            "garages": [],
        }

    garage_ids = [g.id for g in garages]
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    # 1. Query active sessions count per garage
    active_stmt = (
        select(ParkingSession.garage_id, func.count(ParkingSession.id))
        .where(
            ParkingSession.garage_id.in_(garage_ids),
            ParkingSession.status == SessionStatus.ACTIVE,
        )
        .group_by(ParkingSession.garage_id)
    )
    active_res = await db.execute(active_stmt)
    active_by_garage = dict(active_res.all())

    # 2. Query today revenue per garage
    rev_stmt = (
        select(ParkingSession.garage_id, func.coalesce(func.sum(ParkingSession.amount_charged), 0))
        .where(
            ParkingSession.garage_id.in_(garage_ids),
            ParkingSession.status == SessionStatus.COMPLETED,
            ParkingSession.exit_time >= today_start,
        )
        .group_by(ParkingSession.garage_id)
    )
    rev_res = await db.execute(rev_stmt)
    revenue_by_garage = dict(rev_res.all())

    garage_summaries = []
    total_capacity = 0
    total_active = 0
    total_revenue = 0.0

    for g in garages:
        active_count = active_by_garage.get(g.id, 0)
        revenue_amount = float(revenue_by_garage.get(g.id, 0))
        occupancy_pct = round((active_count / g.capacity) * 100, 1) if g.capacity > 0 else 0.0

        total_capacity += g.capacity
        total_active += active_count
        total_revenue += revenue_amount

        garage_summaries.append({
            "id": g.id,
            "name": g.name,
            "code": g.code,
            "capacity": g.capacity,
            "active_sessions": active_count,
            "occupancy_pct": occupancy_pct,
            "today_revenue_egp": revenue_amount,
            "status": g.status,
            "can_use_anpr": g.can_use_anpr,
            "can_use_subscriptions": g.can_use_subscriptions,
        })

    return {
        "owner_id": target_owner_id,
        "total_garages": len(garages),
        "total_capacity": total_capacity,
        "total_active_sessions": total_active,
        "total_today_revenue_egp": total_revenue,
        "garages": garage_summaries,
    }


__all__ = ["router"]
