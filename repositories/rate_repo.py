from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from models.pricing_rule import PricingRule

class PricingRuleRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_active(self, garage_id: int) -> PricingRule | None:
        """Fetches the current active pricing rule for a specific garage."""
        result = await self.db.execute(
            select(PricingRule).where(
                PricingRule.garage_id == garage_id,
                PricingRule.is_active == True,
            ).limit(1)
        )
        return result.scalars().first()

    async def get_by_id(self, rule_id: int, garage_id: int | None = None) -> PricingRule | None:
        """Fetches a pricing rule by its id (and optionally scoped by garage_id)."""
        query = select(PricingRule).where(PricingRule.id == rule_id)
        if garage_id is not None:
            query = query.where(PricingRule.garage_id == garage_id)
        result = await self.db.execute(query.limit(1))
        return result.scalars().first()

    async def get_by_label(self, label: str, garage_id: int) -> PricingRule | None:
        """Fetches a pricing rule by its label within a garage."""
        result = await self.db.execute(
            select(PricingRule).where(
                PricingRule.garage_id == garage_id,
                PricingRule.label == label,
            ).limit(1)
        )
        return result.scalars().first()

    async def get_all(
        self, garage_id: int | None = None, page: int = 1, size: int = 20
    ) -> tuple[list[PricingRule], int]:
        """Returns a paginated list of pricing rules and the total count."""
        query = select(PricingRule)
        count_query = select(func.count()).select_from(PricingRule)

        if garage_id is not None:
            query = query.where(PricingRule.garage_id == garage_id)
            count_query = count_query.where(PricingRule.garage_id == garage_id)

        count_result = await self.db.execute(count_query)
        total_count = count_result.scalar_one()

        offset_val = (page - 1) * size
        query = query.order_by(PricingRule.id.desc()).offset(offset_val).limit(size)
        result = await self.db.execute(query)
        rules = list(result.scalars().all())

        return rules, total_count

    async def create(self, **kwargs) -> PricingRule:
        """Creates a pricing rule, flushing changes."""
        rule = PricingRule(**kwargs)
        self.db.add(rule)
        await self.db.flush()
        return rule

    async def set_active(self, rule_id: int, garage_id: int) -> PricingRule:
        """Sets the pricing rule as active for the specified garage, deactivating all other rules in that garage."""
        rule = await self.get_by_id(rule_id, garage_id=garage_id)
        if not rule:
            raise ValueError(f"PricingRule with id {rule_id} not found in garage {garage_id}")

        # Deactivate all rules for this garage only
        await self.db.execute(
            update(PricingRule)
            .where(PricingRule.garage_id == garage_id)
            .values(is_active=False)
        )

        # Activate the targeted rule
        rule.is_active = True
        await self.db.flush()
        return rule

    async def update(self, rule_id: int, garage_id: int | None = None, **kwargs) -> PricingRule | None:
        """Updates a pricing rule by id."""
        rule = await self.get_by_id(rule_id, garage_id=garage_id)
        if not rule:
            return None
        for key, value in kwargs.items():
            if value is not None and hasattr(rule, key):
                setattr(rule, key, value)
        await self.db.flush()
        return rule

__all__ = ["PricingRuleRepository"]
