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


async def score_article(llm, headline: str, body: str | None) -> dict:
    """
    Score a single article's sentiment using the LLM.
    Returns {"score": float, "label": str}.
    Falls back to {"score": 0.0, "label": "neutral"} on parse failure.
    """
    prompt = _SCORE_PROMPT.format(headline=headline, body=(body or "")[:800])
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        raw = response.content.strip()
        match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
            score = float(data.get("score", 0.0))
            label = data.get("label", "neutral")
            score = max(-1.0, min(1.0, score))
            if label not in ("positive", "neutral", "negative"):
                label = "neutral"
            return {"score": score, "label": label}
    except Exception as exc:
        log.debug("score_article parse failed: %s", exc)
    return {"score": 0.0, "label": "neutral"}


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
    for row in rows:
        try:
            result = await score_article(llm, row["headline"], row["body"])
            await pool.execute(
                "UPDATE news SET sentiment_score = $1, sentiment_label = $2 WHERE id = $3",
                result["score"], result["label"], row["id"],
            )
            scored += 1
        except Exception as exc:
            log.warning("score_article failed id=%s: %s", row["id"], exc)
    log.info("sentiment.scored count=%d", scored)
    return scored
