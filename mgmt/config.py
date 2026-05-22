from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://dse:changeme@localhost:5432/dse_intelligence"
    database_sync_url: str = "postgresql+psycopg://dse:changeme@localhost:5432/dse_intelligence"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # App
    secret_key: str = "dev-secret-change-in-production"
    environment: str = "development"
    log_level: str = "INFO"

    # Management API
    mgmt_api_key: str = ""
    mgmt_api_host: str = "0.0.0.0"
    mgmt_api_port: int = 8001
    mgmt_api_cors_origins: str = "http://localhost:3000"

    # Ops agent
    ops_agent_provider: str = "openrouter"
    ops_agent_model: str = "deepseek/deepseek-v4-flash:free"
    ops_agent_auto_execute_risk: str = "low"

    # Anthropic
    anthropic_api_key: str = ""

    # OpenRouter (cloud)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Ollama (local)
    ollama_base_url: str = "http://localhost:11434"

    # Google Generative AI (Gemini)
    google_api_key: str = ""

    # Scheduler
    scheduler_timezone: str = "Asia/Dhaka"
    live_prices_interval_minutes: int = 5
    market_indices_interval_minutes: int = 5
    fundamentals_interval_hours: int = 6
    announcements_interval_hours: int = 1
    news_interval_minutes: int = 30
    macro_interval_hours: int = 24
    health_check_interval_hours: int = 6

    # Alerts — email
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    alert_email_to: str = ""

    # Alerts — WhatsApp / Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = "whatsapp:+14155238886"
    twilio_whatsapp_to: str = ""

    # Observability
    grafana_port: int = 3001
    grafana_admin_password: str = "admin"
    prometheus_port: int = 9090


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
