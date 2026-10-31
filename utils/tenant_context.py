from dataclasses import dataclass

@dataclass(frozen=True)
class TenantContext:
    garage_id: int | None = None
    owner_id: int | None = None
    is_super_admin: bool = False
    is_system: bool = False
    user_id: int | None = None
    role: str | None = None

__all__ = ["TenantContext"]
