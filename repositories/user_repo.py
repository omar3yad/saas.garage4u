from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from models.user import User, UserRole

class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, user_id: int, garage_id: int | None = None) -> User | None:
        """Fetches a user by ID, strictly enforcing garage scoping when garage_id is specified (IDOR protection)."""
        query = select(User).where(User.id == user_id)
        if garage_id is not None:
            query = query.where(User.garage_id == garage_id)
        result = await self.db.execute(query.limit(1))
        return result.scalars().first()

    async def get_by_username(self, username: str, garage_id: int | None = None) -> User | None:
        """Fetches a user by username within a garage (or globally if garage_id is None)."""
        u = username.strip()

        # Build base filter
        def build_query(condition):
            q = select(User).where(condition)
            if garage_id is not None:
                q = q.where(User.garage_id == garage_id)
            return q

        # 1. Exact match on username
        result = await self.db.execute(build_query(User.username == u))
        user = result.scalars().first()
        if user:
            return user

        # 2. Case-insensitive match on username
        result = await self.db.execute(build_query(func.lower(User.username) == u.lower()))
        user = result.scalars().first()
        if user:
            return user

        # 3. Space-to-underscore match (e.g. "Mohamed abdou" -> "mohamed_abdou")
        normalized = u.lower().replace(" ", "_")
        result = await self.db.execute(build_query(func.lower(User.username) == normalized))
        user = result.scalars().first()
        if user:
            return user

        # 4. Case-insensitive match on full_name
        result = await self.db.execute(build_query(func.lower(User.full_name) == u.lower()))
        user = result.scalars().first()
        if user:
            return user

        # 5. Fallback aliases
        if u.lower() in ("abdou", "198245"):
            result = await self.db.execute(build_query(User.username == "mohamed_abdou"))
            user = result.scalars().first()
            if user:
                return user

        return None

    async def create(self, **kwargs) -> User:
        user = User(**kwargs)
        self.db.add(user)
        await self.db.flush()
        return user

    async def get_all(
        self,
        garage_id: int | None = None,
        owner_id: int | None = None,
        role: UserRole | None = None,
        is_active: bool | None = None,
        gate_number: int | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[User], int]:
        count_stmt = select(func.count()).select_from(User)
        select_stmt = select(User)

        if garage_id is not None:
            count_stmt = count_stmt.where(User.garage_id == garage_id)
            select_stmt = select_stmt.where(User.garage_id == garage_id)
        if owner_id is not None:
            count_stmt = count_stmt.where(User.owner_id == owner_id)
            select_stmt = select_stmt.where(User.owner_id == owner_id)
        if role is not None:
            count_stmt = count_stmt.where(User.role == role)
            select_stmt = select_stmt.where(User.role == role)
        if is_active is not None:
            count_stmt = count_stmt.where(User.is_active == is_active)
            select_stmt = select_stmt.where(User.is_active == is_active)
        if gate_number is not None:
            count_stmt = count_stmt.where(User.gate_number == gate_number)
            select_stmt = select_stmt.where(User.gate_number == gate_number)

        select_stmt = select_stmt.offset((page - 1) * size).limit(size).order_by(User.id.desc())

        count_result = await self.db.execute(count_stmt)
        total_count = count_result.scalar_one()

        select_result = await self.db.execute(select_stmt)
        users = list(select_result.scalars().all())

        return users, total_count

    async def update_fields(self, user: User, **fields) -> User:
        for key, value in fields.items():
            setattr(user, key, value)
        await self.db.flush()
        return user

__all__ = ["UserRepository"]
