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

    # agent
    agent_provider: str = "openrouter"
    agent_model: str = "gemini-2.5-flash"
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

    # Google Cloud Natural Language API GOOGLE_CLOUD_API_KEY (NER for news ticker extraction)
    google_cloud_api_key: str = ""

    # ── Scheduler ────────────────────────────────────────────────────────
    scheduler_timezone: str = "Asia/Dhaka"

    # live_prices: cron every N minutes during market hours (Sun–Thu 10:00–14:30)
    live_prices_market_days: str = "mon,tue,wed,thu,sun"
    live_prices_market_open_hour: int = 10
    live_prices_market_close_hour: int = 14
    live_prices_minutes: str = "0,15,30,45"

    # eod_snapshot: cron at HH:MM on market days
    eod_snapshot_hour: int = 14
    eod_snapshot_minute: int = 35

    # announcements: interval every N hours (full 406-ticker scrape, ~12 min; ON CONFLICT deduplicates)
    announcements_interval_hours: int = 24

    # news_scrape: interval every N hours (free NL API tier: 5k req/month → 12h = ~3k/month)
    news_scrape_interval_hours: int = 12

    # daily_macro: cron at HH:MM every day
    daily_macro_hour: int = 2
    daily_macro_minute: int = 0

    # weekly_fundamentals: cron on day_of_week at HH:MM
    weekly_fundamentals_day: str = "sun"
    weekly_fundamentals_hour: int = 23
    weekly_fundamentals_minute: int = 0

    # monthly: cron on day N of month at HH:MM
    monthly_day: int = 1
    monthly_hour: int = 1
    monthly_minute: int = 0

    # quarterly_retrain: cron on months/day at HH:MM
    quarterly_months: str = "1,4,7,10"
    quarterly_day: int = 1
    quarterly_hour: int = 3
    quarterly_minute: int = 0

    # health_checks: interval every N hours
    health_check_interval_hours: int = 6

    # ── Pipeline test mode ────────────────────────────────────────────────
    # Set PIPELINE_TEST_MODE=true to compress all intervals to minutes.
    # Lets you verify the full pipeline runs without silent failures in ~1h.
    pipeline_test_mode: bool = False
    # Compressed intervals (minutes) used when pipeline_test_mode=true
    test_live_prices_minutes: int = 2
    test_eod_snapshot_minutes: int = 5
    test_announcements_minutes: int = 5
    test_news_scrape_minutes: int = 8
    test_daily_macro_minutes: int = 7
    test_weekly_fundamentals_minutes: int = 10
    test_monthly_minutes: int = 15
    test_quarterly_minutes: int = 20
    test_health_check_minutes: int = 5

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

    # Alerts — Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Cache TTLs (seconds)
    cache_ttl_live_prices: int = 300
    cache_ttl_market_summary: int = 900
    cache_ttl_fundamentals: int = 86400
    cache_ttl_sector_pe: int = 3600
    cache_ttl_pipeline_status: int = 30

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
