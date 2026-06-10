# chat/sentiment.py
"""
Gemini Flash sentiment scoring for news articles.
Scores articles where sentiment_score IS NULL (not yet scored by Google NL API).
"""
from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import HumanMessage

log = logging.getLogger(__name__)

_SCORE_PROMPT = """\
Analyze the sentiment of this financial news article about DSE (Bangladesh stock market).
Respond ONLY with valid JSON: {{"score": <float -1.0 to 1.0>, "label": "<positive|neutral|negative>"}}

Headline: {headline}
Body: {body}

JSON only, no explanation:\
"""


async def score_article(llm, headline: str, body: str | None) -> dict | None:
    """
    Score a single article's sentiment using the LLM.

    Returns {"score": float, "label": str} on a successful parse.
    Returns None when the response cannot be parsed — "could not score" is NOT
    the same as neutral, so the caller must skip the row rather than persist a
    fabricated neutral value (this was the silent-poisoning bug).

    Raises on LLM/API failure (e.g. 429 RESOURCE_EXHAUSTED). The caller decides
    how to handle a dead provider; it must never be swallowed into a fake score.
    """
    prompt = _SCORE_PROMPT.format(headline=headline, body=(body or "")[:800])
    # NOTE: llm.invoke errors propagate intentionally — see docstring.
    response = llm.invoke([HumanMessage(content=prompt)])
    raw = response.content.strip()
    try:
        match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
            score = float(data.get("score", 0.0))
            label = data.get("label", "neutral")
            score = max(-1.0, min(1.0, score))
            if label not in ("positive", "neutral", "negative"):
                label = "neutral"
            return {"score": score, "label": label}
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        log.debug("score_article parse failed: %s", exc)
    return None


async def score_new_articles(pool, llm, limit: int = 100) -> int:
    """
    Score news articles where sentiment_score IS NULL.
    Returns count of articles scored.
    """
    rows = await pool.fetch(
        """
        SELECT id, headline, body FROM news
        WHERE sentiment_score IS NULL
        ORDER BY published_at DESC
        LIMIT $1
        """,
        limit,
    )
    scored = 0
    skipped = 0
    for row in rows:
        # LLM/API errors (e.g. 429) propagate and abort the batch — the job is
        # then recorded as failed rather than silently "scoring" fake neutrals.
        result = await score_article(llm, row["headline"], row["body"])
        if result is None:
            # Unparseable response: leave sentiment_score NULL so the next run
            # retries this row instead of persisting a fabricated neutral.
            skipped += 1
            continue
        await pool.execute(
            "UPDATE news SET sentiment_score = $1, sentiment_label = $2 WHERE id = $3",
            result["score"], result["label"], row["id"],
        )
        scored += 1
    log.info("sentiment.scored count=%d skipped=%d", scored, skipped)
    return scored
