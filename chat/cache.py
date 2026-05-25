# chat/cache.py
"""
Optional Gemini CachedContent API for system prompt caching.
Requires google-generativeai (transitively installed via langchain-google-genai).
Only active when enabled=True and provider=google.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

log = logging.getLogger(__name__)


def _cache_key(text: str) -> str:
    """Short hash key for cache deduplication."""
    return hashlib.sha256(text.encode()).hexdigest()[:8]


class GeminiContextCache:
    def __init__(self, enabled: bool, api_key: str, model: str = "models/gemini-2.5-flash") -> None:
        self.enabled = enabled
        self._api_key = api_key
        self._model = model
        self._cache: dict[str, str] = {}  # key -> cache resource name

    def get_or_create(
        self,
        system_text: str,
        ttl_minutes: int = 30,
    ) -> str | None:
        """
        Return Gemini CachedContent resource name (for reuse), or None if disabled.
        Creates a new cache entry if this system_text hasn't been cached yet.
        """
        if not self.enabled:
            return None

        key = _cache_key(system_text)
        if key in self._cache:
            log.debug("cache.hit key=%s", key)
            return self._cache[key]

        try:
            import google.generativeai as genai
            genai.configure(api_key=self._api_key)
            cached = genai.caching.CachedContent.create(
                model=self._model,
                display_name=f"dse_stock_system_{key}",
                system_instruction=system_text,
                ttl=timedelta(minutes=ttl_minutes),
            )
            self._cache[key] = cached.name
            log.info("cache.created name=%s key=%s ttl=%dm", cached.name, key, ttl_minutes)
            return cached.name
        except Exception as exc:
            log.warning("cache.create_failed key=%s: %s", key, exc)
            return None

    def invalidate(self, system_text: str) -> None:
        key = _cache_key(system_text)
        self._cache.pop(key, None)
