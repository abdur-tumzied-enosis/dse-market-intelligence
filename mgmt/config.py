from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:5432/dse"
    redis_url: str = "redis://localhost:6379/0"
    anthropic_api_key: str = ""
    mgmt_api_key: str = ""  # empty = no auth
    secret_key: str = "dev-secret-change-in-production"

    # Ops agent
    ops_agent_model: str = "claude-haiku-4-5-20251001"
    # "low" = auto-execute low-risk actions; "none" = queue everything for approval
    ops_agent_auto_execute_risk: str = "low"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
