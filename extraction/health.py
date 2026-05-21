"""Source health checking and structure hash monitoring."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


def compute_structure_hash(content: str | bytes) -> str:
    """MD5 hash of HTML table header structure — used to detect silent scrape breakage."""
    if isinstance(content, str):
        content = content.encode()
    return hashlib.md5(content, usedforsecurity=False).hexdigest()


def extract_table_headers(html: str) -> list[str]:
    """Extract all <th> text content from HTML for structure hashing."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    return [th.get_text(strip=True) for th in soup.find_all("th")]


async def check_source_health(url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    """
    Ping a URL and return health metadata.
    Returns dict with: reachable, status_code, response_ms, checked_at, structure_hash (if HTML).
    """
    checked_at = datetime.now(timezone.utc)
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            start = datetime.now(timezone.utc)
            resp = await client.get(url, timeout=timeout)
            elapsed_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000

        content_type = resp.headers.get("content-type", "")
        structure_hash = None
        if "text/html" in content_type and resp.status_code == 200:
            headers = extract_table_headers(resp.text)
            if headers:
                structure_hash = compute_structure_hash("\n".join(headers))

        return {
            "reachable": resp.status_code < 500,
            "status_code": resp.status_code,
            "response_ms": round(elapsed_ms, 1),
            "checked_at": checked_at.isoformat(),
            "structure_hash": structure_hash,
        }
    except Exception as exc:
        logger.warning("health_check_failed", url=url, error=str(exc))
        return {
            "reachable": False,
            "status_code": None,
            "response_ms": None,
            "checked_at": checked_at.isoformat(),
            "structure_hash": None,
            "error": str(exc),
        }
