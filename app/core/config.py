from functools import lru_cache
from typing import List

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "سامانه آزمون آنلاین"
    ENVIRONMENT: str = "development"  # development | production
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    DATABASE_URL: str = "sqlite+aiosqlite:///./exam.db"

    # local | zarinpal_sandbox | zarinpal_live
    PAYMENT_MODE: str = "local"
    ZARINPAL_MERCHANT: str = ""
    PAYMENT_CALLBACK_BASE_URL: str = "http://localhost:8000"

    FRONTEND_URL: str = "http://localhost:5173"
    CORS_ORIGINS: str = "http://localhost:5173"

    # ----- licensing (single-client activation gate) -----
    # LICENSE_HASH: sha256 of the license secret, hex. Empty = gate disabled.
    # LICENSE_ENABLED: set to true in production to arm the gate (dev/test never lock).
    # LICENSE_FILE: where the activation marker (hash of the entered key) is stored.
    #                Keep it OUT of the source tree so redeploys don't wipe the activation.
    LICENSE_HASH: str = ""
    LICENSE_ENABLED: bool = False
    LICENSE_FILE: str = "data/license.dat"

    RATE_LIMIT_ENABLED: bool = True
    REGISTER_RATE_LIMIT: str = "5/minute"
    LOGIN_RATE_LIMIT: str = "5/minute"
    # 10/hour so the pay->start->resume flow (up to ~3 calls) never trips a legitimate student
    EXAM_START_RATE_LIMIT: str = "10/hour"
    FRAUD_RATE_LIMIT: str = "30/minute"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def _enforce_production_secret(self) -> "Settings":
        if self.ENVIRONMENT == "production" and self.SECRET_KEY == "dev-secret-key-change-in-production":
            raise ValueError("SECRET_KEY must be overridden in production")
        return self

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
