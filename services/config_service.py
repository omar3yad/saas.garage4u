from typing import Any
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from models.garage_config import GarageConfig
from repositories.garage_config_repo import GarageConfigRepository
from repositories.garage_repo import GarageRepository
from repositories.session_repo import ParkingSessionRepository
from schemas.garage_config import GarageConfigUpdate
from services.exceptions import (
    TenantNotFoundError,
    FeatureNotEntitledError,
    ExitPhotoRequiresANPRError,
    ConfigLockedActiveSessionsError,
    ConfigVersionConflictError,
)

class ConfigService:
    def __init__(
        self,
        db: AsyncSession,
        config_repo: GarageConfigRepository | None = None,
        garage_repo: GarageRepository | None = None,
        session_repo: ParkingSessionRepository | None = None,
    ):
        self.db = db
        self.config_repo = config_repo or GarageConfigRepository(db)
        self.garage_repo = garage_repo or GarageRepository(db)
        self.session_repo = session_repo or ParkingSessionRepository(db)

    async def get_effective_config(self, garage_id: int) -> dict[str, Any]:
        """Calculates and returns effective permissions and configuration settings for a garage:
        Effective Permission = Entitlement and Setting.
        """
        garage = await self.garage_repo.get_by_id(garage_id)
        if not garage:
            raise TenantNotFoundError(f"Garage with id {garage_id} not found")

        config = await self.config_repo.get_by_garage_id(garage_id)
        if not config:
            raise TenantNotFoundError(f"Configuration for garage {garage_id} not found")

        effective_anpr = bool(garage.can_use_anpr and config.anpr_enabled)
        effective_subscriptions = bool(garage.can_use_subscriptions and config.subscriptions_enabled)

        return {
            "id": config.id,
            "garage_id": config.garage_id,
            "ticket_system": config.ticket_system,
            "anpr_enabled": config.anpr_enabled,
            "exit_photo_enabled": config.exit_photo_enabled,
            "subscriptions_enabled": config.subscriptions_enabled,
            "require_shift_cash_reconciliation": config.require_shift_cash_reconciliation,
            "min_session_duration_seconds": config.min_session_duration_seconds,
            "receipt_settings": config.receipt_settings,
            "version": config.version,
            "effective_anpr": effective_anpr,
            "effective_subscriptions": effective_subscriptions,
            "can_use_anpr": garage.can_use_anpr,
            "can_use_subscriptions": garage.can_use_subscriptions,
            "updated_at": config.updated_at,
        }

    async def update_config(
        self,
        garage_id: int,
        update_data: GarageConfigUpdate | dict[str, Any],
        expected_version: int,
    ) -> GarageConfig:
        """Applies configuration updates with:
        1. Entitlement checks (FeatureNotEntitledError)
        2. Operational consistency checks (ExitPhotoRequiresANPRError)
        3. Active sessions locks (ConfigLockedActiveSessionsError)
        4. Optimistic concurrency locking (ConfigVersionConflictError)
        """
        garage = await self.garage_repo.get_by_id(garage_id)
        if not garage:
            raise TenantNotFoundError(f"Garage with id {garage_id} not found")

        current_config = await self.config_repo.get_by_garage_id(garage_id)
        if not current_config:
            raise TenantNotFoundError(f"Configuration for garage {garage_id} not found")

        if isinstance(update_data, BaseModel):
            fields = update_data.model_dump(exclude_unset=True)
        else:
            fields = dict(update_data)

        # Remove keys that cannot be modified directly
        fields.pop("id", None)
        fields.pop("garage_id", None)
        fields.pop("version", None)
        fields.pop("updated_at", None)

        if "receipt_settings" in fields and isinstance(fields["receipt_settings"], BaseModel):
            fields["receipt_settings"] = fields["receipt_settings"].model_dump()

        # 1. Entitlement verification
        if fields.get("anpr_enabled") is True and not garage.can_use_anpr:
            raise FeatureNotEntitledError("ANPR feature is not entitled for this garage")

        if fields.get("subscriptions_enabled") is True and not garage.can_use_subscriptions:
            raise FeatureNotEntitledError("Subscriptions feature is not entitled for this garage")

        # 2. Operational consistency check (merged state)
        candidate_anpr = fields.get("anpr_enabled", current_config.anpr_enabled)
        candidate_exit_photo = fields.get("exit_photo_enabled", current_config.exit_photo_enabled)

        if candidate_exit_photo is True:
            if not candidate_anpr or not garage.can_use_anpr:
                raise ExitPhotoRequiresANPRError("Exit photo requires ANPR to be entitled and enabled")

        # 3. Active sessions lock on ticket_system or gate mode changes
        new_ticket_system = fields.get("ticket_system")
        if new_ticket_system is not None and new_ticket_system != current_config.ticket_system:
            active_count = await self.session_repo.count_active(garage_id)
            if active_count > 0:
                raise ConfigLockedActiveSessionsError(
                    f"Cannot change ticket system while {active_count} active sessions exist"
                )

        # 4. Optimistic locking update
        updated = await self.config_repo.update_with_optimistic_lock(
            garage_id=garage_id,
            expected_version=expected_version,
            **fields,
        )
        if updated is None:
            raise ConfigVersionConflictError(
                f"Configuration was modified by another transaction (expected version {expected_version})"
            )

        return updated

__all__ = ["ConfigService"]
