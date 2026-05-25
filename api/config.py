from __future__ import annotations
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class APISettings(BaseSettings):
    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret_key: str = "dev-secret-change-in-production"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_cors_origins: str = "http://localhost:3000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> APISettings:
    return APISettings()
