from datetime import datetime
import math
from types import SimpleNamespace
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.pricing_rule import PricingRule
from repositories.rate_repo import PricingRuleRepository
from schemas.pricing_rule import PricingRuleCreate, PricingRuleUpdate
from services.audit_service import AuditService
from services.exceptions import NoPricingRuleError, RateLabelAlreadyExistsError
from services.pricing_calculation import PriceCalculation

class PricingService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_active_rule(self, garage_id: int | None = None) -> PricingRule:
        """Fetches the active pricing rule from the database, scoped to garage_id when provided."""
        query = select(PricingRule).where(PricingRule.is_active == True)
        if garage_id is not None:
            query = query.where(PricingRule.garage_id == garage_id)
        result = await self.db.execute(query.limit(1))
        rule = result.scalars().first()
        if not rule and garage_id is not None:
            result = await self.db.execute(
                select(PricingRule).where(PricingRule.is_active == True).limit(1)
            )
            rule = result.scalars().first()
        if not rule:
            raise NoPricingRuleError("No active pricing rule")
        return rule

    def calculate(
        self,
        session: Any,
        rule: PricingRule,
        exit_time: datetime,
    ) -> PriceCalculation:
        """Synchronously and purely calculates pricing for a parking session.

        Tiered pricing logic:
        - If first_hour_charge > 0: first hour costs first_hour_charge,
          each additional hour costs subsequent_hour_charge.
        - Otherwise falls back to flat rate_per_hour × billable_hours.
        """
        # Ensure timezone-naive datetimes for calculation
        entry = session.entry_time
        if entry.tzinfo is not None:
            entry = entry.replace(tzinfo=None)
        exit_dt = exit_time
        if exit_dt.tzinfo is not None:
            exit_dt = exit_dt.replace(tzinfo=None)

        total_seconds = (exit_dt - entry).total_seconds()
        duration_minutes = math.ceil(total_seconds / 60)
        if duration_minutes < 0:
            duration_minutes = 0

        first_hour = getattr(rule, "first_hour_charge", 0) or 0
        subsequent = getattr(rule, "subsequent_hour_charge", 0) or 0

        if duration_minutes <= rule.grace_period_mins:
            is_grace_period = True
            billable_minutes = 0
            billable_hours = 0
            base_amount = rule.minimum_charge
        else:
            is_grace_period = False
            billable_minutes = duration_minutes - rule.grace_period_mins
            billable_hours = math.ceil(billable_minutes / 60)

            if first_hour > 0:
                # Tiered: 1st hour = first_hour_charge, each extra = subsequent_hour_charge
                if billable_hours <= 1:
                    raw = first_hour
                else:
                    raw = first_hour + (billable_hours - 1) * subsequent
            else:
                # Flat rate fallback
                raw = billable_hours * rule.rate_per_hour

            base_amount = max(raw, rule.minimum_charge)

        penalty_amount = 0
        total_amount = base_amount

        return PriceCalculation(
            duration_minutes=duration_minutes,
            billable_minutes=billable_minutes,
            billable_hours=billable_hours,
            rate_per_hour=rule.rate_per_hour,
            first_hour_charge=first_hour,
            subsequent_hour_charge=subsequent,
            grace_period_mins=rule.grace_period_mins,
            minimum_charge=rule.minimum_charge,
            base_amount=base_amount,
            penalty_amount=penalty_amount,
            total_amount=total_amount,
            pricing_rule_id=rule.id,
            is_grace_period=is_grace_period,
            is_lost_card=False,
        )


    def calculate_lost_card(
        self,
        session: Any,
        rule: PricingRule,
        exit_time: datetime,
    ) -> PriceCalculation:
        """Calculates pricing for a parking session when the card is lost."""
        base_calc = self.calculate(session, rule, exit_time)
        return PriceCalculation(
            duration_minutes=base_calc.duration_minutes,
            billable_minutes=base_calc.billable_minutes,
            billable_hours=base_calc.billable_hours,
            rate_per_hour=base_calc.rate_per_hour,
            first_hour_charge=base_calc.first_hour_charge,
            subsequent_hour_charge=base_calc.subsequent_hour_charge,
            grace_period_mins=base_calc.grace_period_mins,
            minimum_charge=base_calc.minimum_charge,
            base_amount=base_calc.base_amount,
            penalty_amount=rule.lost_card_penalty,
            total_amount=base_calc.base_amount + rule.lost_card_penalty,
            pricing_rule_id=rule.id,
            is_grace_period=base_calc.is_grace_period,
            is_lost_card=True,
        )

    async def preview(self, entry_time: datetime) -> PriceCalculation:
        """Fetches the active rule and previews the price for the given entry time."""
        rule = await self.get_active_rule()
        if entry_time.tzinfo is not None:
            entry_time = entry_time.replace(tzinfo=None)
        mock_session = SimpleNamespace(entry_time=entry_time)
        return self.calculate(mock_session, rule, datetime.utcnow())

    async def create_rule(
        self,
        data: PricingRuleCreate,
        admin_id: int,
        rate_repo: PricingRuleRepository,
        audit_service: AuditService,
    ) -> PricingRule:
        """Creates a new pricing rule with uniqueness check and audit logging."""
        existing = await rate_repo.get_by_label(data.label)
        if existing:
            raise RateLabelAlreadyExistsError(f"Pricing rule with label '{data.label}' already exists")

        effective_from = data.effective_from or datetime.utcnow()
        rule = await rate_repo.create(
            label=data.label,
            rate_per_hour=data.rate_per_hour,
            first_hour_charge=data.first_hour_charge,
            subsequent_hour_charge=data.subsequent_hour_charge,
            minimum_charge=data.minimum_charge,
            grace_period_mins=data.grace_period_mins,
            lost_card_penalty=data.lost_card_penalty,
            effective_from=effective_from,
            effective_until=data.effective_until,
            created_by=admin_id,
            is_active=False,
        )
        await self.db.commit()
        await self.db.refresh(rule)

        await audit_service.log(
            actor_id=admin_id,
            action="RATE_CREATED",
            entity_type="pricing_rule",
            entity_id=rule.id,
            after={
                "label": rule.label,
                "rate_per_hour": rule.rate_per_hour,
                "first_hour_charge": rule.first_hour_charge,
                "subsequent_hour_charge": rule.subsequent_hour_charge,
                "minimum_charge": rule.minimum_charge,
                "grace_period_mins": rule.grace_period_mins,
                "lost_card_penalty": rule.lost_card_penalty,
                "effective_from": rule.effective_from.isoformat() if rule.effective_from else None,
            },
        )
        await self.db.commit()
        return rule

    async def update_rule(
        self,
        rule_id: int,
        data: PricingRuleUpdate,
        admin_id: int,
        rate_repo: PricingRuleRepository,
        audit_service: AuditService,
        garage_id: int | None = None,
    ) -> PricingRule:
        """Updates an existing pricing rule with uniqueness check and audit logging."""
        rule = await rate_repo.get_by_id(rule_id, garage_id=garage_id)
        if not rule:
            raise ValueError(f"PricingRule with id {rule_id} not found")

        if data.label and data.label != rule.label:
            existing = await rate_repo.get_by_label(data.label)
            if existing and existing.id != rule_id:
                raise RateLabelAlreadyExistsError(f"Pricing rule with label '{data.label}' already exists")

        before_state = {
            "label": rule.label,
            "rate_per_hour": rule.rate_per_hour,
            "first_hour_charge": rule.first_hour_charge,
            "subsequent_hour_charge": rule.subsequent_hour_charge,
            "minimum_charge": rule.minimum_charge,
            "grace_period_mins": rule.grace_period_mins,
            "lost_card_penalty": rule.lost_card_penalty,
        }

        update_dict = {}
        if data.label is not None:
            update_dict["label"] = data.label
        if data.rate_per_hour is not None:
            update_dict["rate_per_hour"] = data.rate_per_hour
        if data.first_hour_charge is not None:
            update_dict["first_hour_charge"] = data.first_hour_charge
        if data.subsequent_hour_charge is not None:
            update_dict["subsequent_hour_charge"] = data.subsequent_hour_charge
        if data.minimum_charge is not None:
            update_dict["minimum_charge"] = data.minimum_charge
        if data.grace_period_mins is not None:
            update_dict["grace_period_mins"] = data.grace_period_mins
        if data.lost_card_penalty is not None:
            update_dict["lost_card_penalty"] = data.lost_card_penalty
        if data.effective_from is not None:
            update_dict["effective_from"] = data.effective_from
        if data.effective_until is not None:
            update_dict["effective_until"] = data.effective_until

        rule = await rate_repo.update(rule_id, **update_dict)
        await self.db.commit()
        await self.db.refresh(rule)

        await audit_service.log(
            actor_id=admin_id,
            action="RATE_UPDATED",
            entity_type="pricing_rule",
            entity_id=rule.id,
            before=before_state,
            after={
                "label": rule.label,
                "rate_per_hour": rule.rate_per_hour,
                "first_hour_charge": rule.first_hour_charge,
                "subsequent_hour_charge": rule.subsequent_hour_charge,
                "minimum_charge": rule.minimum_charge,
                "grace_period_mins": rule.grace_period_mins,
                "lost_card_penalty": rule.lost_card_penalty,
            },
        )
        await self.db.commit()
        return rule


__all__ = ["PricingService"]
