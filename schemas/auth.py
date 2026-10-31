from pydantic import BaseModel, Field
from models.user import UserRole

class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    garage_code: str | None = Field(default=None)

class LoginResponse(BaseModel):
    user_id: int
    role: UserRole
    full_name: str

class TokenPayload(BaseModel):
    sub: str
    role: str
    iat: int
    exp: int

class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=4, max_length=128)

__all__ = [
    "LoginRequest",
    "LoginResponse",
    "TokenPayload",
    "ChangePasswordRequest",
]
