# tests/unit/test_sentiment.py
from unittest.mock import AsyncMock, MagicMock

import pytest


class _Row(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


@pytest.mark.asyncio
async def test_score_article_positive():
    from chat.sentiment import score_article
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content='{"score": 0.75, "label": "positive"}')
    result = await score_article(mock_llm, "BRACBANK profits surge 30%", "BRACBANK reported strong Q3 earnings.")
    assert result["label"] in ("positive", "neutral", "negative")
    assert -1.0 <= result["score"] <= 1.0


@pytest.mark.asyncio
async def test_score_article_returns_none_on_unparseable():
    """Unparseable LLM output means 'could not score' — NOT neutral.

    Writing neutral here is the silent-poisoning bug: a malformed response is
    not evidence of neutral sentiment. Return None so the caller skips the row.
    """
    from chat.sentiment import score_article
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content="I think it is positive overall.")
    result = await score_article(mock_llm, "headline", "body")
    assert result is None


@pytest.mark.asyncio
async def test_score_article_propagates_api_error():
    """An LLM/API error (e.g. 429 RESOURCE_EXHAUSTED) must propagate, not be
    swallowed into a fake neutral score."""
    from chat.sentiment import score_article
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED")
    with pytest.raises(RuntimeError):
        await score_article(mock_llm, "headline", "body")


@pytest.mark.asyncio
async def test_score_article_clamps_score():
    from chat.sentiment import score_article
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content='{"score": 2.5, "label": "positive"}')
    result = await score_article(mock_llm, "headline", "body")
    assert result["score"] <= 1.0


@pytest.mark.asyncio
async def test_score_article_invalid_label_defaults_neutral():
    from chat.sentiment import score_article
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content='{"score": 0.3, "label": "bullish"}')
    result = await score_article(mock_llm, "headline", "body")
    assert result["label"] == "neutral"


@pytest.mark.asyncio
async def test_score_new_articles_updates_records():
    from chat.sentiment import score_new_articles
    pool = AsyncMock()
    pool.fetch.return_value = [
        _Row(id=1, headline="GP profits up", body="Good results"),
        _Row(id=2, headline="Market falls", body="Bad session"),
    ]
    pool.execute.return_value = None

    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content='{"score": 0.5, "label": "positive"}')

    count = await score_new_articles(pool, mock_llm, limit=5)
    assert count == 2
    assert pool.execute.call_count == 2


@pytest.mark.asyncio
async def test_score_new_articles_zero_when_empty():
    from chat.sentiment import score_new_articles
    pool = AsyncMock()
    pool.fetch.return_value = []
    mock_llm = MagicMock()
    count = await score_new_articles(pool, mock_llm, limit=5)
    assert count == 0


@pytest.mark.asyncio
async def test_score_new_articles_skips_unparseable_rows():
    """Rows the LLM can't score (None) are left untouched (sentiment_score stays
    NULL → retried next run), not written as fake neutral."""
    from chat.sentiment import score_new_articles
    pool = AsyncMock()
    pool.fetch.return_value = [
        _Row(id=1, headline="GP profits up", body="Good results"),
        _Row(id=2, headline="garbled", body="garbled"),
    ]
    pool.execute.return_value = None

    mock_llm = MagicMock()
    # row 1 parses, row 2 is unparseable
    mock_llm.invoke.side_effect = [
        MagicMock(content='{"score": 0.5, "label": "positive"}'),
        MagicMock(content="no json here"),
    ]

    count = await score_new_articles(pool, mock_llm, limit=5)
    assert count == 1
    assert pool.execute.call_count == 1


@pytest.mark.asyncio
async def test_score_new_articles_propagates_api_error_without_writing():
    """A 429/API error must abort the batch and propagate — never silently
    write neutral for the failed rows (the production-poisoning bug)."""
    from chat.sentiment import score_new_articles
    pool = AsyncMock()
    pool.fetch.return_value = [
        _Row(id=1, headline="GP profits up", body="Good results"),
    ]
    pool.execute.return_value = None

    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED")

    with pytest.raises(RuntimeError):
        await score_new_articles(pool, mock_llm, limit=5)
    assert pool.execute.call_count == 0
