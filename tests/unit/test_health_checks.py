"""
Unit tests for health checking and alert firing.

- extraction/health.py  — check_source_health (httpx mocked)
- extraction/observability.py — run_health_checks (DB + HTTP mocked, fire_alert spy)
- extraction/scheduler.py — health_checks job registration
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


# ── helpers ────────────────────────────────────────────────────────────

def _mock_pool(prev_hash: str | None = None) -> AsyncMock:
    """asyncpg pool stub with configurable previous structure hash."""
    pool = AsyncMock()
    pool.fetchrow.return_value = {"structure_hash": prev_hash} if prev_hash is not None else None
    pool.execute.return_value = None
    return pool


def _health_result(
    *,
    reachable: bool = True,
    status_code: int | None = 200,
    response_ms: float | None = 120.0,
    structure_hash: str | None = None,
    error: str | None = None,
) -> dict:
    r = {
        "reachable": reachable,
        "status_code": status_code,
        "response_ms": response_ms,
        "checked_at": "2026-01-01T00:00:00+00:00",
        "structure_hash": structure_hash,
    }
    if error:
        r["error"] = error
    return r


# ── check_source_health ────────────────────────────────────────────────

class TestCheckSourceHealth:
    """Mock httpx at extraction.health.httpx.AsyncClient."""

    def _mock_client(self, status: int, content_type: str = "application/json", text: str = "") -> AsyncMock:
        resp = MagicMock()
        resp.status_code = status
        resp.headers = {"content-type": content_type}
        resp.text = text

        client = AsyncMock()
        client.get.return_value = resp
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        return client

    @pytest.mark.asyncio
    async def test_reachable_json_url(self):
        client = self._mock_client(200, "application/json")
        with patch("extraction.health.httpx.AsyncClient", return_value=client):
            from extraction.health import check_source_health
            result = await check_source_health("https://api.example.com/data")

        assert result["reachable"] is True
        assert result["status_code"] == 200
        assert result["structure_hash"] is None  # no HTML headers to hash

    @pytest.mark.asyncio
    async def test_connection_exception_returns_unreachable(self):
        client = AsyncMock()
        client.get.side_effect = Exception("Connection refused")
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch("extraction.health.httpx.AsyncClient", return_value=client):
            from extraction.health import check_source_health
            result = await check_source_health("https://down.example.com")

        assert result["reachable"] is False
        assert result["status_code"] is None
        assert result["response_ms"] is None
        assert "Connection refused" in result["error"]

    @pytest.mark.asyncio
    async def test_5xx_status_counts_as_unreachable(self):
        client = self._mock_client(503, "text/html")
        with patch("extraction.health.httpx.AsyncClient", return_value=client):
            from extraction.health import check_source_health
            result = await check_source_health("https://flaky.example.com")

        assert result["reachable"] is False
        assert result["status_code"] == 503

    @pytest.mark.asyncio
    async def test_4xx_status_still_counts_as_reachable(self):
        client = self._mock_client(404, "text/html")
        with patch("extraction.health.httpx.AsyncClient", return_value=client):
            from extraction.health import check_source_health
            result = await check_source_health("https://example.com/gone")

        assert result["reachable"] is True  # < 500 means server responded
        assert result["status_code"] == 404

    @pytest.mark.asyncio
    async def test_html_with_table_headers_produces_structure_hash(self):
        html = "<table><thead><tr><th>Symbol</th><th>Price</th></tr></thead></table>"
        client = self._mock_client(200, "text/html; charset=utf-8", html)
        with patch("extraction.health.httpx.AsyncClient", return_value=client):
            from extraction.health import check_source_health
            result = await check_source_health("https://dsebd.org/prices")

        assert result["reachable"] is True
        assert result["structure_hash"] is not None
        assert len(result["structure_hash"]) == 32  # MD5 hex string

    @pytest.mark.asyncio
    async def test_html_without_table_headers_no_hash(self):
        html = "<html><body><p>No tables here.</p></body></html>"
        client = self._mock_client(200, "text/html", html)
        with patch("extraction.health.httpx.AsyncClient", return_value=client):
            from extraction.health import check_source_health
            result = await check_source_health("https://example.com")

        assert result["structure_hash"] is None

    @pytest.mark.asyncio
    async def test_response_ms_populated_on_success(self):
        client = self._mock_client(200)
        with patch("extraction.health.httpx.AsyncClient", return_value=client):
            from extraction.health import check_source_health
            result = await check_source_health("https://api.example.com")

        assert result["response_ms"] is not None
        assert isinstance(result["response_ms"], float)


# ── run_health_checks — alert routing ─────────────────────────────────

class TestRunHealthChecks:
    """Mock DB pool + check_source_health; spy on fire_alert."""

    @pytest.mark.asyncio
    async def test_source_down_fires_critical_alert(self):
        pool = _mock_pool()
        down = _health_result(reachable=False, status_code=None, response_ms=None, error="timeout")

        with (
            patch("db.pool.get_pool", AsyncMock(return_value=pool)),
            patch("extraction.observability.check_source_health", AsyncMock(return_value=down)),
            patch("extraction.observability.fire_alert", new_callable=AsyncMock) as mock_alert,
        ):
            from extraction.observability import run_health_checks
            await run_health_checks()

        # 5 sources × 1 CRITICAL each
        assert mock_alert.call_count == 5
        severities = {c.kwargs["severity"] for c in mock_alert.call_args_list}
        assert severities == {"CRITICAL"}

    @pytest.mark.asyncio
    async def test_only_dse_direct_down_fires_one_critical(self):
        from extraction.observability import SOURCE_URLS

        pool = _mock_pool()
        reachable = _health_result()
        down = _health_result(reachable=False, status_code=None, response_ms=None, error="refused")

        async def _health_by_url(url: str, **_):
            if url == SOURCE_URLS["dse_direct"]:
                return down
            return reachable

        with (
            patch("db.pool.get_pool", AsyncMock(return_value=pool)),
            patch("extraction.observability.check_source_health", side_effect=_health_by_url),
            patch("extraction.observability.fire_alert", new_callable=AsyncMock) as mock_alert,
        ):
            from extraction.observability import run_health_checks
            await run_health_checks()

        assert mock_alert.call_count == 1
        call_kwargs = mock_alert.call_args.kwargs
        assert call_kwargs["severity"] == "CRITICAL"
        assert "dse_direct" in call_kwargs["message"]

    @pytest.mark.asyncio
    async def test_structure_hash_change_fires_warning(self):
        pool = _mock_pool(prev_hash="oldhash456")
        result = _health_result(structure_hash="newhash123")

        with (
            patch("db.pool.get_pool", AsyncMock(return_value=pool)),
            patch("extraction.observability.check_source_health", AsyncMock(return_value=result)),
            patch("extraction.observability.fire_alert", new_callable=AsyncMock) as mock_alert,
        ):
            from extraction.observability import run_health_checks
            await run_health_checks()

        warning_calls = [c for c in mock_alert.call_args_list if c.kwargs["severity"] == "WARNING"]
        assert len(warning_calls) == 5  # all 5 sources have changed hash
        details = warning_calls[0].kwargs["details"]
        assert details["old_hash"] == "oldhash456"
        assert details["new_hash"] == "newhash123"

    @pytest.mark.asyncio
    async def test_same_hash_as_previous_no_alert(self):
        pool = _mock_pool(prev_hash="stablehash")
        result = _health_result(structure_hash="stablehash")

        with (
            patch("db.pool.get_pool", AsyncMock(return_value=pool)),
            patch("extraction.observability.check_source_health", AsyncMock(return_value=result)),
            patch("extraction.observability.fire_alert", new_callable=AsyncMock) as mock_alert,
        ):
            from extraction.observability import run_health_checks
            await run_health_checks()

        mock_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_previous_hash_no_warning_on_first_run(self):
        """First ever check — no prev row in DB → no hash-change alert."""
        pool = _mock_pool(prev_hash=None)
        result = _health_result(structure_hash="firsthash")

        with (
            patch("db.pool.get_pool", AsyncMock(return_value=pool)),
            patch("extraction.observability.check_source_health", AsyncMock(return_value=result)),
            patch("extraction.observability.fire_alert", new_callable=AsyncMock) as mock_alert,
        ):
            from extraction.observability import run_health_checks
            await run_health_checks()

        mock_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_details_contain_source_url_and_error(self):
        from extraction.observability import SOURCE_URLS

        pool = _mock_pool()
        down = _health_result(reachable=False, status_code=None, response_ms=None, error="Name resolution failed")

        with (
            patch("db.pool.get_pool", AsyncMock(return_value=pool)),
            patch("extraction.observability.check_source_health", AsyncMock(return_value=down)),
            patch("extraction.observability.fire_alert", new_callable=AsyncMock) as mock_alert,
        ):
            from extraction.observability import run_health_checks
            await run_health_checks()

        for c in mock_alert.call_args_list:
            details = c.kwargs["details"]
            assert "source" in details
            assert "url" in details
            assert "error" in details
            assert details["url"] == SOURCE_URLS[details["source"]]


# ── fire_alert — DB write + notification routing ───────────────────────

class TestFireAlert:
    @pytest.mark.asyncio
    async def test_critical_alert_writes_to_db(self):
        pool = _mock_pool()
        with (
            patch("db.pool.get_pool", AsyncMock(return_value=pool)),
            patch("extraction.observability._send_email_alert", AsyncMock(return_value=False)),
            patch("extraction.observability._send_telegram_alert", AsyncMock(return_value=False)),
            patch("extraction.observability._send_whatsapp_alert", AsyncMock(return_value=False)),
        ):
            from extraction.observability import fire_alert
            await fire_alert(severity="CRITICAL", message="Test source down", details={"source": "test"})

        pool.execute.assert_called()
        insert_call = pool.execute.call_args_list[0]
        sql = insert_call.args[0]
        assert "INSERT INTO pipeline_alerts" in sql

    @pytest.mark.asyncio
    async def test_critical_attempts_email_telegram_and_whatsapp(self):
        pool = _mock_pool()
        mock_email = AsyncMock(return_value=False)
        mock_tg = AsyncMock(return_value=False)
        mock_wa = AsyncMock(return_value=False)

        with (
            patch("db.pool.get_pool", AsyncMock(return_value=pool)),
            patch("extraction.observability._send_email_alert", mock_email),
            patch("extraction.observability._send_telegram_alert", mock_tg),
            patch("extraction.observability._send_whatsapp_alert", mock_wa),
        ):
            from extraction.observability import fire_alert
            await fire_alert(severity="CRITICAL", message="down", details={})

        mock_email.assert_called_once()
        mock_tg.assert_called_once()
        mock_wa.assert_called_once()

    @pytest.mark.asyncio
    async def test_warning_attempts_email_and_telegram_not_whatsapp(self):
        pool = _mock_pool()
        mock_email = AsyncMock(return_value=False)
        mock_tg = AsyncMock(return_value=False)
        mock_wa = AsyncMock(return_value=False)

        with (
            patch("db.pool.get_pool", AsyncMock(return_value=pool)),
            patch("extraction.observability._send_email_alert", mock_email),
            patch("extraction.observability._send_telegram_alert", mock_tg),
            patch("extraction.observability._send_whatsapp_alert", mock_wa),
        ):
            from extraction.observability import fire_alert
            await fire_alert(severity="WARNING", message="hash changed", details={})

        mock_email.assert_called_once()
        mock_tg.assert_called_once()
        mock_wa.assert_not_called()

    @pytest.mark.asyncio
    async def test_info_alert_skips_all_notifications(self):
        pool = _mock_pool()
        mock_email = AsyncMock(return_value=False)
        mock_tg = AsyncMock(return_value=False)

        with (
            patch("db.pool.get_pool", AsyncMock(return_value=pool)),
            patch("extraction.observability._send_email_alert", mock_email),
            patch("extraction.observability._send_telegram_alert", mock_tg),
        ):
            from extraction.observability import fire_alert
            await fire_alert(severity="INFO", message="all ok", details={})

        mock_email.assert_not_called()
        mock_tg.assert_not_called()


# ── _send_telegram_alert — unit tests ─────────────────────────────────

class TestSendTelegramAlert:
    def _mock_httpx(self, status: int = 200, json_body: dict | None = None) -> AsyncMock:
        resp = MagicMock()
        resp.status_code = status
        resp.text = str(json_body or {})

        client = AsyncMock()
        client.post.return_value = resp
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        return client

    @pytest.mark.asyncio
    async def test_skips_when_token_not_set(self):
        with patch.dict("os.environ", {}, clear=False):
            os_env = {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "12345"}
            with patch.dict("os.environ", os_env):
                from extraction.observability import _send_telegram_alert
                result = await _send_telegram_alert("CRITICAL", "down", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_skips_when_chat_id_not_set(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "abc:123", "TELEGRAM_CHAT_ID": ""}):
            from extraction.observability import _send_telegram_alert
            result = await _send_telegram_alert("CRITICAL", "down", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_sends_message_and_returns_true_on_200(self):
        client = self._mock_httpx(200)
        env = {"TELEGRAM_BOT_TOKEN": "abc:123", "TELEGRAM_CHAT_ID": "-100987654"}

        with (
            patch.dict("os.environ", env),
            patch("extraction.observability.httpx.AsyncClient", return_value=client),
        ):
            from extraction.observability import _send_telegram_alert
            result = await _send_telegram_alert("CRITICAL", "dse_direct down", {"source": "dse_direct"})

        assert result is True
        client.post.assert_called_once()
        call_kwargs = client.post.call_args
        assert "bot{}/sendMessage".format(env["TELEGRAM_BOT_TOKEN"]) in call_kwargs.args[0]
        payload = call_kwargs.kwargs["json"]
        assert payload["chat_id"] == env["TELEGRAM_CHAT_ID"]
        assert "dse_direct down" in payload["text"]
        assert payload["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_returns_false_on_non_200(self):
        client = self._mock_httpx(400)
        env = {"TELEGRAM_BOT_TOKEN": "abc:123", "TELEGRAM_CHAT_ID": "-100987654"}

        with (
            patch.dict("os.environ", env),
            patch("extraction.observability.httpx.AsyncClient", return_value=client),
        ):
            from extraction.observability import _send_telegram_alert
            result = await _send_telegram_alert("WARNING", "hash changed", {})

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_network_exception(self):
        client = AsyncMock()
        client.post.side_effect = Exception("network error")
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        env = {"TELEGRAM_BOT_TOKEN": "abc:123", "TELEGRAM_CHAT_ID": "-100987654"}

        with (
            patch.dict("os.environ", env),
            patch("extraction.observability.httpx.AsyncClient", return_value=client),
        ):
            from extraction.observability import _send_telegram_alert
            result = await _send_telegram_alert("CRITICAL", "down", {})

        assert result is False

    @pytest.mark.asyncio
    async def test_critical_message_includes_fire_emoji(self):
        client = self._mock_httpx(200)
        env = {"TELEGRAM_BOT_TOKEN": "abc:123", "TELEGRAM_CHAT_ID": "-100987654"}

        with (
            patch.dict("os.environ", env),
            patch("extraction.observability.httpx.AsyncClient", return_value=client),
        ):
            from extraction.observability import _send_telegram_alert
            await _send_telegram_alert("CRITICAL", "source down", {})

        payload = client.post.call_args.kwargs["json"]
        assert "🚨" in payload["text"]

    @pytest.mark.asyncio
    async def test_warning_message_includes_warning_emoji(self):
        client = self._mock_httpx(200)
        env = {"TELEGRAM_BOT_TOKEN": "abc:123", "TELEGRAM_CHAT_ID": "-100987654"}

        with (
            patch.dict("os.environ", env),
            patch("extraction.observability.httpx.AsyncClient", return_value=client),
        ):
            from extraction.observability import _send_telegram_alert
            await _send_telegram_alert("WARNING", "hash changed", {})

        payload = client.post.call_args.kwargs["json"]
        assert "⚠️" in payload["text"]


# ── scheduler — interval config ────────────────────────────────────────

@pytest.mark.skipif(
    __import__("importlib").util.find_spec("apscheduler") is None,
    reason="apscheduler not installed",
)
class TestSchedulerHealthCheckJob:
    def _mock_settings(self, health_check_interval_hours: int = 1) -> MagicMock:
        cfg = MagicMock()
        cfg.health_check_interval_hours = health_check_interval_hours
        cfg.announcements_interval_hours = 2
        cfg.live_prices_market_days = "mon"
        cfg.live_prices_market_open_hour = 10
        cfg.live_prices_market_close_hour = 14
        cfg.live_prices_minutes = "0,30"
        cfg.eod_snapshot_hour = 14
        cfg.eod_snapshot_minute = 35
        cfg.daily_macro_hour = 2
        cfg.daily_macro_minute = 0
        cfg.weekly_fundamentals_day = "sun"
        cfg.weekly_fundamentals_hour = 23
        cfg.weekly_fundamentals_minute = 0
        cfg.monthly_day = 1
        cfg.monthly_hour = 1
        cfg.monthly_minute = 0
        cfg.quarterly_months = "1,4,7,10"
        cfg.quarterly_day = 1
        cfg.quarterly_hour = 3
        cfg.quarterly_minute = 0
        cfg.pipeline_test_mode = False
        cfg.news_scrape_interval_hours = 12
        return cfg

    def _capture_jobs(self, scheduler: MagicMock) -> list[dict]:
        jobs = []
        def _capture(fn, trigger=None, **kwargs):
            jobs.append({"fn": fn, "trigger": trigger, **kwargs})
        scheduler.add_job.side_effect = _capture
        return jobs

    def test_health_check_job_registered_as_interval(self):
        scheduler = MagicMock()
        jobs = self._capture_jobs(scheduler)
        cfg = self._mock_settings(health_check_interval_hours=1)

        with patch("mgmt.config.get_settings", return_value=cfg):
            from extraction.scheduler import configure_scheduler
            configure_scheduler(scheduler)

        health_jobs = [j for j in jobs if j.get("id") == "health_checks"]
        assert len(health_jobs) == 1
        assert health_jobs[0]["trigger"] == "interval"

    def test_health_check_interval_respects_config(self):
        scheduler = MagicMock()
        jobs = self._capture_jobs(scheduler)
        cfg = self._mock_settings(health_check_interval_hours=1)

        with patch("mgmt.config.get_settings", return_value=cfg):
            from extraction.scheduler import configure_scheduler
            configure_scheduler(scheduler)

        health_job = next(j for j in jobs if j.get("id") == "health_checks")
        assert health_job["hours"] == 1

    def test_all_16_jobs_registered(self):
        scheduler = MagicMock()
        jobs = self._capture_jobs(scheduler)
        cfg = self._mock_settings()

        with patch("mgmt.config.get_settings", return_value=cfg):
            from extraction.scheduler import configure_scheduler
            configure_scheduler(scheduler)

        assert len(jobs) == 16

    def test_health_check_interval_reads_env_var(self, monkeypatch):
        """Set HEALTH_CHECK_INTERVAL_HOURS=1 to run checks every hour instead of every 6."""
        monkeypatch.setenv("HEALTH_CHECK_INTERVAL_HOURS", "1")
        # Clear pydantic-settings cache so env var is picked up
        import importlib
        import mgmt.config as cfg_mod
        importlib.reload(cfg_mod)

        from mgmt.config import Settings
        settings = Settings()
        assert settings.health_check_interval_hours == 1

    @pytest.mark.asyncio
    async def test_job_health_checks_calls_run_health_checks(self):
        mock_run = AsyncMock()
        # job_health_checks does `from extraction.observability import run_health_checks`
        # at call time — patching the attribute on the loaded module is enough
        import extraction.observability as obs_mod
        with patch.object(obs_mod, "run_health_checks", mock_run):
            from extraction.scheduler import job_health_checks
            await job_health_checks()
        mock_run.assert_awaited_once()
