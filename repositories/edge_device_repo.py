from datetime import datetime
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from models.edge_device import EdgeDevice, EdgeDeviceGate

class EdgeDeviceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_token_hash(self, token_hash: str) -> EdgeDevice | None:
        """Global authentication lookup without garage context for WebSocket/Upload handshake."""
        result = await self.db.execute(
            select(EdgeDevice).where(
                EdgeDevice.token_hash == token_hash,
                EdgeDevice.revoked_at.is_(None),
            ).limit(1)
        )
        return result.scalars().first()

    async def get_by_id(self, device_id: int, garage_id: int | None = None) -> EdgeDevice | None:
        query = select(EdgeDevice).where(EdgeDevice.id == device_id)
        if garage_id is not None:
            query = query.where(EdgeDevice.garage_id == garage_id)
        result = await self.db.execute(query.limit(1))
        return result.scalars().first()

    async def get_by_garage(self, garage_id: int) -> list[EdgeDevice]:
        result = await self.db.execute(
            select(EdgeDevice).where(EdgeDevice.garage_id == garage_id).order_by(EdgeDevice.id.asc())
        )
        return list(result.scalars().all())

    async def create(self, **kwargs) -> EdgeDevice:
        device = EdgeDevice(**kwargs)
        self.db.add(device)
        await self.db.flush()
        return device

    async def update_last_seen(self, device_id: int) -> None:
        device = await self.get_by_id(device_id)
        if device:
            device.last_seen_at = datetime.utcnow()
            await self.db.flush()

    async def revoke(self, device_id: int, garage_id: int) -> EdgeDevice | None:
        device = await self.get_by_id(device_id, garage_id=garage_id)
        if device:
            device.revoked_at = datetime.utcnow()
            await self.db.flush()
        return device

    async def assign_gate(
        self, device_id: int, garage_id: int, gate_number: int, direction: str = "BIDIRECTIONAL"
    ) -> EdgeDeviceGate:
        result = await self.db.execute(
            select(EdgeDeviceGate).where(
                EdgeDeviceGate.device_id == device_id,
                EdgeDeviceGate.garage_id == garage_id,
                EdgeDeviceGate.gate_number == gate_number,
                EdgeDeviceGate.direction == direction,
            ).limit(1)
        )
        existing = result.scalars().first()
        if existing:
            existing.is_active = True
            await self.db.flush()
            return existing

        assignment = EdgeDeviceGate(
            device_id=device_id,
            garage_id=garage_id,
            gate_number=gate_number,
            direction=direction,
            is_active=True,
        )
        self.db.add(assignment)
        await self.db.flush()
        return assignment

    async def get_device_gates(self, device_id: int, garage_id: int) -> list[EdgeDeviceGate]:
        result = await self.db.execute(
            select(EdgeDeviceGate).where(
                EdgeDeviceGate.device_id == device_id,
                EdgeDeviceGate.garage_id == garage_id,
            )
        )
        return list(result.scalars().all())

__all__ = ["EdgeDeviceRepository"]
