# tests/unit/test_sentiment.py
import pytest
from unittest.mock import AsyncMock, MagicMock


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
async def test_score_article_handles_malformed_response():
    from chat.sentiment import score_article
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content="I think it is positive overall.")
    result = await score_article(mock_llm, "headline", "body")
    assert "score" in result
    assert "label" in result
    assert result["label"] == "neutral"


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
