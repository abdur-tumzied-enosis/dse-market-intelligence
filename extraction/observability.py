"""
Pipeline observability — health checks + alert firing.

run_health_checks()  → populates source_health table (call every 6h)
fire_alert()         → writes to pipeline_alerts + routes notifications
"""
from __future__ import annotations

import os
import smtplib
import asyncio
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Any

import structlog

from extraction.health import check_source_health

logger = structlog.get_logger(__name__)

# Primary URL to ping for each data source.
SOURCE_URLS: dict[str, str] = {
    "dse_direct":      "https://www.dsebd.org/latest_share_price_scroll_l.php",
    "amarstock":       "https://www.amarstock.com",
    "bsec":            "https://sec.gov.bd/home",
    "worldbank":       "https://api.worldbank.org/v2/country/BD/indicator/FP.CPI.TOTL.ZG?format=json&mrv=1",
    "bangladesh_bank": "https://www.bb.org.bd/en/index.php",
}


async def run_health_checks() -> None:
    """Ping all sources, write results to source_health, fire alerts on failures."""
    from db.pool import get_pool

    pool = await get_pool()
    now = datetime.now(timezone.utc)

    for source_name, url in SOURCE_URLS.items():
        result = await check_source_health(url)

        # Fetch previous hash for change detection
        prev_row = await pool.fetchrow(
            """
            SELECT structure_hash FROM source_health
            WHERE source_name = $1
            ORDER BY checked_at DESC
            LIMIT 1
            """,
            source_name,
        )
        prev_hash = prev_row["structure_hash"] if prev_row else None

        await pool.execute(
            """
            INSERT INTO source_health
                (checked_at, source_name, url, reachable, status_code,
                 response_ms, structure_hash, prev_hash)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            now,
            source_name,
            url,
            result["reachable"],
            result.get("status_code"),
            int(result["response_ms"]) if result.get("response_ms") else None,
            result.get("structure_hash"),
            prev_hash,
        )

        logger.info(
            "health_check",
            source=source_name,
            reachable=result["reachable"],
            status_code=result.get("status_code"),
            response_ms=result.get("response_ms"),
        )

        if not result["reachable"]:
            await fire_alert(
                severity="CRITICAL",
                message=f"Source unreachable: {source_name} ({url})",
                stream_name=None,
                details={
                    "source": source_name,
                    "url": url,
                    "error": result.get("error", ""),
                    "status_code": result.get("status_code"),
                },
            )

        elif result.get("structure_hash") and prev_hash and result["structure_hash"] != prev_hash:
            await fire_alert(
                severity="WARNING",
                message=f"Structure hash changed for {source_name} — scraper may be broken",
                stream_name=None,
                details={
                    "source": source_name,
                    "url": url,
                    "old_hash": prev_hash,
                    "new_hash": result["structure_hash"],
                },
            )


async def fire_alert(
    severity: str,
    message: str,
    stream_name: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """
    Write alert to pipeline_alerts table and route notifications.

    severity: 'CRITICAL' | 'WARNING' | 'INFO'
    CRITICAL → email + WhatsApp
    WARNING  → email only
    INFO     → DB only
    """
    import json
    from db.pool import get_pool

    details = details or {}
    notified_via: list[str] = []

    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO pipeline_alerts
            (severity, stream_name, message, details)
        VALUES ($1, $2, $3, $4)
        """,
        severity,
        stream_name,
        message,
        json.dumps(details),
    )

    logger.warning("alert_fired", severity=severity, message=message, details=details)

    if severity in ("CRITICAL", "WARNING"):
        sent = await _send_email_alert(severity, message, details)
        if sent:
            notified_via.append("email")

    if severity == "CRITICAL":
        sent = await _send_whatsapp_alert(message, details)
        if sent:
            notified_via.append("whatsapp")

    if notified_via:
        await pool.execute(
            """
            UPDATE pipeline_alerts
            SET notified_via = $1
            WHERE id = (SELECT MAX(id) FROM pipeline_alerts WHERE message = $2)
            """,
            notified_via,
            message,
        )


async def _send_email_alert(severity: str, message: str, details: dict[str, Any]) -> bool:
    """Send email alert via SMTP. Returns True if sent successfully."""
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASSWORD", "")
    alert_to  = os.environ.get("ALERT_EMAIL_TO", "")

    if not all([smtp_host, smtp_user, smtp_pass, alert_to]):
        logger.debug("email_alert_skipped: SMTP not configured")
        return False

    subject = f"[DSE Pipeline {severity}] {message[:80]}"
    body = f"{severity}: {message}\n\nDetails:\n" + "\n".join(
        f"  {k}: {v}" for k, v in details.items()
    )
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = alert_to

    def _send() -> None:
        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.send_message(msg)

    try:
        await asyncio.get_event_loop().run_in_executor(None, _send)
        logger.info("email_alert_sent", to=alert_to, severity=severity)
        return True
    except Exception as exc:
        logger.warning("email_alert_failed", error=str(exc))
        return False


async def _send_whatsapp_alert(message: str, details: dict[str, Any]) -> bool:
    """Send WhatsApp alert via Twilio. Returns True if sent successfully."""
    return False  # Disable WhatsApp alerts for now to avoid accidental messages during testing
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token  = os.environ.get("TWILIO_AUTH_TOKEN", "")
    from_number = os.environ.get("TWILIO_WHATSAPP_FROM", "")  # whatsapp:+14155238886
    to_number   = os.environ.get("TWILIO_WHATSAPP_TO", "")    # whatsapp:+8801XXXXXXXXX

    if not all([account_sid, auth_token, from_number, to_number]):
        logger.debug("whatsapp_alert_skipped: Twilio not configured")
        return False

    try:
        from twilio.rest import Client  # type: ignore[import]
        body = f"🚨 DSE Pipeline CRITICAL\n{message}"
        if details:
            body += "\n" + "\n".join(f"{k}: {v}" for k, v in list(details.items())[:3])

        def _send() -> None:
            client = Client(account_sid, auth_token)
            client.messages.create(body=body, from_=from_number, to=to_number)

        await asyncio.get_event_loop().run_in_executor(None, _send)
        logger.info("whatsapp_alert_sent", to=to_number)
        return True
    except ImportError:
        logger.debug("whatsapp_alert_skipped: twilio not installed (pip install twilio)")
        return False
    except Exception as exc:
        logger.warning("whatsapp_alert_failed", error=str(exc))
        return False
