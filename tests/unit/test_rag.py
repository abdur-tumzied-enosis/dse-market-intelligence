# tests/unit/test_rag.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class _Row(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


@pytest.mark.asyncio
async def test_embed_query_returns_list_of_floats():
    from chat.rag import embed_query
    mock_embed = MagicMock()
    mock_embed.embed_query.return_value = [0.1] * 768
    with patch("chat.rag._get_embedder", return_value=mock_embed):
        result = embed_query("BRACBANK earnings")
    assert isinstance(result, list)
    assert len(result) == 768
    assert all(isinstance(x, float) for x in result)


@pytest.mark.asyncio
async def test_search_chunks_returns_list():
    from chat.rag import search_chunks
    pool = AsyncMock()
    pool.fetch.return_value = [
        _Row(chunk_text="BRACBANK reported strong Q3 results", ticker="BRACBANK", doc_type="news", chunk_index=0, similarity=0.9),
    ]
    embedding = [0.1] * 768
    result = await search_chunks(pool, embedding, top_k=3)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["chunk_text"] == "BRACBANK reported strong Q3 results"


@pytest.mark.asyncio
async def test_build_rag_context_empty_when_no_chunks():
    from chat.rag import build_rag_context
    pool = AsyncMock()
    pool.fetch.return_value = []
    mock_embed = MagicMock()
    mock_embed.embed_query.return_value = [0.0] * 768
    with patch("chat.rag._get_embedder", return_value=mock_embed):
        ctx = await build_rag_context(pool, "some query")
    assert ctx == ""


@pytest.mark.asyncio
async def test_build_rag_context_formats_chunks():
    from chat.rag import build_rag_context
    pool = AsyncMock()
    pool.fetch.return_value = [
        _Row(chunk_text="BRACBANK Q3 profit up 20%", ticker="BRACBANK", doc_type="news", chunk_index=0, similarity=0.95),
    ]
    mock_embed = MagicMock()
    mock_embed.embed_query.return_value = [0.1] * 768
    with patch("chat.rag._get_embedder", return_value=mock_embed):
        ctx = await build_rag_context(pool, "BRACBANK profit")
    assert "BRACBANK" in ctx
    assert "Knowledge Base" in ctx


@pytest.mark.asyncio
async def test_build_rag_context_graceful_on_error():
    from chat.rag import build_rag_context
    pool = AsyncMock()
    mock_embed = MagicMock()
    mock_embed.embed_query.side_effect = Exception("API error")
    with patch("chat.rag._get_embedder", return_value=mock_embed):
        ctx = await build_rag_context(pool, "any query")
    assert ctx == ""
