from datetime import datetime, timezone, timedelta
import bcrypt
from jose import jwt, JWTError
from config import Settings
from models.user import User
from repositories.user_repo import UserRepository
from schemas.auth import TokenPayload

DUMMY_HASH = "$2b$12$pqd1tgDPY04wL8zVySNatedgTHZLGictaEdPedhCa9M4sf/SnoK.y"

class AuthenticationError(Exception):
    def __init__(self, message: str = "Invalid credentials"):
        super().__init__(message)
        self.message = message

class AuthService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def hash_password(self, plain: str) -> str:
        hashed = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12))
        del plain
        return hashed.decode("utf-8")

    def verify_password(self, plain: str, hashed: str) -> bool:
        res = bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
        del plain
        return res

    def create_access_token(self, user_id: int, role: str) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user_id),
            "role": role,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=self.settings.JWT_EXPIRE_HOURS)).timestamp()),
        }
        return jwt.encode(payload, self.settings.SECRET_KEY, algorithm=self.settings.JWT_ALGORITHM)

    def decode_token(self, token: str) -> TokenPayload:
        try:
            payload = jwt.decode(token, self.settings.SECRET_KEY, algorithms=[self.settings.JWT_ALGORITHM])
            return TokenPayload(**payload)
        except (JWTError, Exception):
            raise AuthenticationError("Token is invalid or expired")

    async def authenticate_user(
        self, username: str, password: str, user_repo: UserRepository, garage_code: str | None = None
    ) -> User:
        user: User | None = None
        if garage_code:
            from repositories.garage_repo import GarageRepository
            garage_repo = GarageRepository(user_repo.db)
            garage = await garage_repo.get_by_code(garage_code.strip())
            if not garage:
                self.verify_password("dummy", DUMMY_HASH)
                raise AuthenticationError("Invalid credentials")
            user = await user_repo.get_by_username(username.strip(), garage_id=garage.id)
        else:
            # Check if multiple users exist with this username across garages
            from sqlalchemy import select, func
            stmt = select(User).where(func.lower(User.username) == username.strip().lower())
            result = await user_repo.db.execute(stmt)
            matched_users = result.scalars().all()
            if len(matched_users) > 1:
                self.verify_password("dummy", DUMMY_HASH)
                raise AuthenticationError("Garage code is required when username exists across multiple garages")
            elif len(matched_users) == 1:
                user = matched_users[0]
            else:
                user = await user_repo.get_by_username(username.strip())

        if not user:
            self.verify_password("dummy", DUMMY_HASH)
            raise AuthenticationError("Invalid credentials")
        
        if not self.verify_password(password, user.hashed_password):
            raise AuthenticationError("Invalid credentials")
            
        if not user.is_active:
            raise AuthenticationError("Invalid credentials")
            
        return user
