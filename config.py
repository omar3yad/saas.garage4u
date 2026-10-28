import os
from typing import Literal
import functools
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    ENVIRONMENT: Literal["development", "staging", "production"]
    DEBUG: bool = False
    APP_NAME: str = "garage4u"
    JWT_EXPIRE_HOURS: int = 24
    JWT_ALGORITHM: str = "HS256"
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    CORS_ORIGINS: list[str] = []
    SEED_ADMIN_USERNAME: str | None = None
    SEED_ADMIN_PASSWORD: str | None = None
    SEED_ADMIN_FULL_NAME: str | None = None

    # Edge plate-recognition (garage PC connects over WebSocket; disabled when unset)
    EDGE_API_TOKEN: str | None = None
    PLATE_CAPTURE_TIMEOUT_S: float = 4.0
    PLATE_POLL_MAX_WAIT_S: float = 6.0
    EDGE_PRESENCE_TTL_S: float = 30.0
    PLATE_IMAGE_DIR: str = "media/plates"
    PLATE_IMAGE_MAX_BYTES: int = 1_500_000

    model_config = SettingsConfigDict(
        env_file=_ENV_PATH,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_settings(self) -> "Settings":
        if len(self.SECRET_KEY) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        if self.ENVIRONMENT == "production" and self.DEBUG is True:
            raise ValueError("DEBUG must not be True in production")
        if self.JWT_EXPIRE_HOURS < 1:
            raise ValueError("JWT_EXPIRE_HOURS must be at least 1")
        if self.EDGE_API_TOKEN is not None and len(self.EDGE_API_TOKEN) < 32:
            raise ValueError("EDGE_API_TOKEN must be at least 32 characters")
        if not 0.5 <= self.PLATE_CAPTURE_TIMEOUT_S <= 30:
            raise ValueError("PLATE_CAPTURE_TIMEOUT_S must be between 0.5 and 30")
        return self

@functools.lru_cache()
def get_settings() -> Settings:
    return Settings()

__all__ = ["Settings", "get_settings"]
