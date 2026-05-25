# chat/rag.py
"""
RAG pipeline: embed query with Google text-embedding-004 -> pgvector cosine search.
Embedding dimension: 768 (Google text-embedding-004 default).
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

log = logging.getLogger(__name__)

_EMBEDDING_MODEL = "models/text-embedding-004"
_TOP_K_DEFAULT = 5


@lru_cache(maxsize=1)
def _get_embedder():
    """Lazy singleton -- avoids importing at module level (no API key needed for tests)."""
    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    from mgmt.config import get_settings
    s = get_settings()
    return GoogleGenerativeAIEmbeddings(
        model=_EMBEDDING_MODEL,
        google_api_key=s.google_api_key,
        task_type="retrieval_query",
    )


def embed_query(text: str) -> list[float]:
    """Embed a query string using Google text-embedding-004 (768 dims)."""
    return _get_embedder().embed_query(text)


async def search_chunks(
    pool,
    embedding: list[float],
    ticker: str | None = None,
    top_k: int = _TOP_K_DEFAULT,
) -> list[dict]:
    """Cosine similarity search on document_chunks using pgvector <=> operator."""
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    if ticker:
        rows = await pool.fetch(
            """
            SELECT chunk_text, ticker, doc_type, chunk_index,
                   1 - (embedding <=> $1::vector) AS similarity
            FROM document_chunks
            WHERE ticker = $2
            ORDER BY embedding <=> $1::vector
            LIMIT $3
            """,
            vec_str, ticker.upper(), top_k,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT chunk_text, ticker, doc_type, chunk_index,
                   1 - (embedding <=> $1::vector) AS similarity
            FROM document_chunks
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            vec_str, top_k,
        )
    return [dict(r) for r in rows]


async def build_rag_context(
    pool,
    query: str,
    ticker: str | None = None,
    top_k: int = _TOP_K_DEFAULT,
) -> str:
    """
    Embed query -> pgvector search -> format top chunks as context string.
    Returns empty string if no chunks found (table is sparse early in deployment).
    """
    try:
        embedding = embed_query(query)
        chunks = await search_chunks(pool, embedding, ticker=ticker, top_k=top_k)
    except Exception as exc:
        log.warning("rag.search_failed: %s", exc)
        return ""

    if not chunks:
        return ""

    lines = ["--- Relevant Context from Knowledge Base ---"]
    for i, chunk in enumerate(chunks, 1):
        src = f"[{chunk.get('doc_type', 'unknown')} / ticker={chunk.get('ticker', 'N/A')}]"
        lines.append(f"{i}. {src}\n   {chunk.get('chunk_text', '')[:500]}")
    return "\n".join(lines)
