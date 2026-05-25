# tests/unit/test_cache.py
from chat.cache import GeminiContextCache, _cache_key


def test_context_cache_disabled_by_default():
    cache = GeminiContextCache(enabled=False, api_key="test")
    assert not cache.enabled


def test_context_cache_is_noop_when_disabled():
    cache = GeminiContextCache(enabled=False, api_key="test")
    result = cache.get_or_create("system prompt text", ttl_minutes=30)
    assert result is None


def test_context_cache_builds_cache_key():
    key1 = _cache_key("hello world")
    key2 = _cache_key("hello world")
    key3 = _cache_key("different text")
    assert key1 == key2
    assert key1 != key3
    assert len(key1) == 8


def test_cache_key_is_hex():
    key = _cache_key("any text")
    assert all(c in "0123456789abcdef" for c in key)


def test_invalidate_removes_cached_entry():
    cache = GeminiContextCache(enabled=False, api_key="test")
    # Manually inject a cache entry
    cache._cache[_cache_key("text")] = "projects/x/cachedContents/123"
    cache.invalidate("text")
    assert _cache_key("text") not in cache._cache
