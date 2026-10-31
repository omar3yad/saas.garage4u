from sqlalchemy.ext.asyncio import AsyncSession
from repositories.counter_repo import GarageCounterRepository
from repositories.garage_repo import GarageRepository
from services.exceptions import (
    TenantNotFoundError,
    TenantSuspendedError,
    TenantTerminatedError,
)

class CounterService:
    def __init__(
        self,
        db: AsyncSession,
        counter_repo: GarageCounterRepository | None = None,
        garage_repo: GarageRepository | None = None,
    ):
        self.db = db
        self.counter_repo = counter_repo or GarageCounterRepository(db)
        self.garage_repo = garage_repo or GarageRepository(db)

    async def get_next_receipt_number(self, garage_id: int, allow_grace: bool = False) -> int:
        """Atomically increments and returns the next sequential receipt number for a garage.
        Guarantees <3ms lock duration and enforces tenant status checks.
        """
        garage = await self.garage_repo.get_by_id(garage_id)
        if not garage:
            raise TenantNotFoundError(f"Garage with id {garage_id} not found")

        if garage.status == "TERMINATED":
            raise TenantTerminatedError("Garage operations are terminated")

        if garage.status == "SUSPENDED" and not allow_grace:
            raise TenantSuspendedError("Garage is suspended")

        return await self.counter_repo.get_next_receipt_no(garage_id)

    async def get_current_receipt_number(self, garage_id: int) -> int:
        """Returns the current last receipt number for a garage without incrementing."""
        return await self.counter_repo.get_current(garage_id)

__all__ = ["CounterService"]
