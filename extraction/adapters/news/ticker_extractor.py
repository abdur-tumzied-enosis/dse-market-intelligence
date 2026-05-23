"""
TickerExtractor — maps article text → DSE ticker symbols + context organisations.

Pipeline:
  1. POST article text to Google Natural Language API (analyzeEntities)
  2. Filter returned entities to type=ORGANIZATION
  3. Route each org into one of two buckets:
       - context_orgs : hits _ORG_BLOCKLIST → kept as KG signal, not matched to ticker
       - tickers      : passes blocklist → fuzzy-matched against DSE company names
  4. Return ExtractionResult(tickers, context_orgs)

Two-bucket design rationale:
  Blocklisted orgs (NBR, Bangladesh Bank, BGMEA, ACC …) are catalysts for stock moves.
  Silently dropping them loses the relationship edge needed by the prediction KG:
      [BRACBANK] ←mentioned_with→ [Bangladesh Bank]
  Saving them as context_orgs (news.context_orgs TEXT[]) preserves this signal
  without polluting the ticker-matching logic.

Usage in scheduler job:
    extractor = TickerExtractor(
        api_key=settings.google_cloud_api_key,
        company_map=await load_company_map(db_pool),
    )
    result = await extractor.extract(article_headline + " " + article_body)
    # result.tickers       → ["BRACBANK", "GP"]
    # result.context_orgs  → ["bangladesh bank", "nbr"]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
import structlog
import httpx
from rapidfuzz import fuzz, process

logger = structlog.get_logger(__name__)

_NL_API_URL = "https://language.googleapis.com/v1/documents:analyzeEntitySentiment"
# analyzeEntitySentiment returns entities WITH per-entity sentiment in one call.
# Separate free-tier quota from analyzeEntities: 5,000 units/month.
# Document-level sentiment is computed as salience-weighted average of entity sentiments.
_FUZZY_THRESHOLD = 85  # Raised from 75: stop-word stripping removes noise so strings
                       # are now denser with unique identifying info — higher bar is safe
_MAX_TEXT_CHARS = 5_000  # NL API char limit per request

_SENTIMENT_POS_THRESHOLD = 0.25   # score > +0.25 → "positive"
_SENTIMENT_NEG_THRESHOLD = -0.25  # score < -0.25 → "negative"

# Orgs that appear in almost every BD financial article as context, not as subject companies.
# Keeping this list comprehensive prevents false-positive ticker matches.
_ORG_BLOCKLIST: frozenset[str] = frozenset({
    # Stock exchanges & regulators
    "dse", "dhaka stock exchange",
    "cse", "chittagong stock exchange",
    "bsec", "securities and exchange commission", "sec",
    "dsex", "ds30", "dses",  # index names

    # Bangladesh central bank & fiscal authority
    "bangladesh bank", "central bank", "bb",
    "nbr", "national board of revenue",

    # Government & legal bodies
    "government", "ministry", "cabinet",
    "parliament", "jatiya sangsad",
    "high court", "supreme court", "appellate division",
    "acc", "anti-corruption commission",
    "cid", "rab", "police",
    "epb", "export promotion bureau",
    "bbs", "bangladesh bureau of statistics",
    "brta", "btrc", "bida", "bepza",

    # International financial institutions
    "world bank", "imf", "ifc", "adb", "asian development bank",
    "un", "united nations", "undp", "usaid", "ifad",
    "fed", "federal reserve", "eu",

    # Media outlets (cited as sources, not companies)
    "the daily star", "daily star",
    "the business standard", "tbs",
    "prothom alo",
    "the financial express", "financial express",
    "bdnews24", "unb", "bss",
    "reuters", "bloomberg",
    "dhaka tribune",

    # Trade associations & chambers
    "bgmea", "bkmea",
    "fbcci", "dcci", "mcci", "ficci",
    "bab", "bangladesh association of banks",
    "baplc",
    "basis",

    # Credit rating agencies (BD + global)
    "crisl", "ecrl", "ncr", "crasl", "emerging credit rating",
    "moody's", "s&p", "fitch", "standard & poor's",

    # Generic terms NL API misclassifies as orgs
    "board of directors", "board", "agm", "egm",
    "fdi", "foreign direct investment",
    "stock market", "capital market", "mutual fund",
})

# Suffixes stripped from both entity names and company_map keys before fuzzy matching.
# Prevents "City Bank PLC" and "Prime Bank PLC" from scoring high against each other
# purely because they share " bank plc" — the distinctive part is "City" vs "Prime".
_FUZZY_STOP_WORDS: frozenset[str] = frozenset({
    "plc", "ltd", "limited", "company", "co",
    "bank", "insurance", "finance", "investment",
    "textiles", "textile", "spinning", "industries", "industry",
    "group", "holdings", "holding", "enterprise", "enterprises",
    "mutual fund", "fund", "trust",
    "bangladesh", "bd",
})


_PUNCT_RE = re.compile(r"[^\w\s]")


@dataclass
class ExtractionResult:
    """Output of TickerExtractor.extract().

    tickers         — matched DSE ticker symbols (e.g. ["BRACBANK", "GP"])
    context_orgs    — blocklisted orgs kept as KG signal (e.g. ["bangladesh bank", "nbr"])
    sentiment_score — salience-weighted average of per-entity sentiment scores (-1.0 to 1.0)
    sentiment_label — "positive" | "neutral" | "negative" derived from sentiment_score
    """
    tickers: list[str] = field(default_factory=list)
    context_orgs: list[str] = field(default_factory=list)
    sentiment_score: float | None = None
    sentiment_label: str | None = None


def _strip_stop_words(name: str) -> str:
    """Normalize company name for fuzzy comparison.

    Steps: lowercase → remove punctuation → split → drop stop words → rejoin.
    Fallback: if all words are stop words (e.g. "Bangladesh Bank Ltd" → ""),
    return the punctuation-cleaned original so we don't match against empty string.

    Examples:
        "City Bank PLC"            → "city"
        "Prime Bank PLC"           → "prime"
        "Square Pharmaceuticals."  → "square"
        "Beximco Pharmaceuticals"  → "beximco"
        "Bangladesh Bank Ltd"      → "bangladesh bank"  (fallback — all stripped)
    """
    cleaned = _PUNCT_RE.sub(" ", name.lower()).strip()
    words = cleaned.split()
    filtered = [w for w in words if w not in _FUZZY_STOP_WORDS]
    return " ".join(filtered) if filtered else cleaned


def _compute_doc_sentiment(entities: list[dict]) -> tuple[float | None, str | None]:
    """Compute document-level sentiment as salience-weighted average of entity sentiments.

    Uses all entity types (not just ORGANIZATION) for a more representative signal.
    Returns (score, label) where score is -1.0..1.0 and label is positive/neutral/negative.
    Returns (None, None) if no entities have sentiment data.

    Per-entity sentiment (from analyzeEntitySentiment) is more informative than a flat
    document score: "NBR freezes BRACBANK accounts" → BRACBANK sentiment may be negative
    even if overall article tone is neutral.
    """
    total_salience = 0.0
    weighted_score = 0.0

    for entity in entities:
        sentiment = entity.get("sentiment", {})
        score = sentiment.get("score")
        salience = entity.get("salience", 0.0)
        if score is None or salience == 0.0:
            continue
        weighted_score += salience * score
        total_salience += salience

    if total_salience == 0.0:
        return None, None

    doc_score = weighted_score / total_salience

    if doc_score > _SENTIMENT_POS_THRESHOLD:
        label = "positive"
    elif doc_score < _SENTIMENT_NEG_THRESHOLD:
        label = "negative"
    else:
        label = "neutral"

    return round(doc_score, 4), label


class TickerExtractor:
    """Extract DSE tickers from free text using Google NL API + fuzzy company name matching."""

    def __init__(self, api_key: str, company_map: dict[str, str]) -> None:
        """
        Args:
            api_key: Google Cloud API key with Natural Language API enabled.
            company_map: built by load_company_map() — includes both company name
                         and ticker symbol as keys so "GP" → "GP" resolves directly.
        """
        self._api_key = api_key
        self._company_map = {k.lower(): v for k, v in company_map.items()}

        # Pre-flight set: all known terms (names + tickers) lowercased.
        # Used to skip the NL API call when article mentions no known BD company at all.
        self._known_terms: frozenset[str] = frozenset(self._company_map.keys())

        # Fuzzy candidates: full company names only (len > 4), with stop words stripped.
        # Stored as (stripped_key, original_key) so we can look up the ticker after match.
        self._fuzzy_stripped: list[str] = []
        self._fuzzy_originals: list[str] = []
        for k in self._company_map:
            if len(k) > 4:
                stripped = _strip_stop_words(k)
                if stripped:
                    self._fuzzy_stripped.append(stripped)
                    self._fuzzy_originals.append(k)

    def _preflight(self, text: str) -> bool:
        """Return True if text contains at least one known company name or ticker symbol.

        Saves an NL API call (~$0.001) for articles that are pure macro/policy news
        with no specific DSE-listed company mentioned.
        """
        text_lower = text.lower()
        return any(term in text_lower for term in self._known_terms)

    async def extract(self, text: str) -> ExtractionResult:
        """Extract DSE tickers and context organisations from article text.

        Returns ExtractionResult with two buckets:
          .tickers      — matched DSE ticker symbols
          .context_orgs — blocklisted orgs (regulatory/macro actors) kept as KG signal
        """
        empty = ExtractionResult()
        if not text or not self._api_key or not self._company_map:
            return empty

        # Pre-flight: skip NL API if article mentions no known DSE company at all.
        # Pure macro/policy articles (e.g. "IMF raises BD growth forecast") save an
        # API call but still get context_orgs=[] since tickers=[] too — acceptable.
        if not self._preflight(text):
            logger.debug("ticker_preflight_skip", text_preview=text[:80])
            return empty

        raw_entities = await self._fetch_entities(text)
        tickers: list[str] = []
        context_orgs: list[str] = []

        for org in (e["name"] for e in raw_entities if e.get("type") == "ORGANIZATION"):
            org_lower = org.lower().strip()

            # Blocklisted → save as context KG node, skip ticker matching
            if org_lower in _ORG_BLOCKLIST:
                context_orgs.append(org_lower)
                continue

            # Layer 1: exact match — full company names and DSE ticker symbols
            if org_lower in self._company_map:
                tickers.append(self._company_map[org_lower])
                continue

            # Layer 2: fuzzy on stop-word-stripped names
            org_stripped = _strip_stop_words(org_lower)
            if len(org_stripped) < 4:
                continue

            result = process.extractOne(
                org_stripped,
                self._fuzzy_stripped,
                scorer=fuzz.WRatio,
                score_cutoff=_FUZZY_THRESHOLD,
            )
            if result:
                matched_stripped, score, idx = result
                original_key = self._fuzzy_originals[idx]
                tickers.append(self._company_map[original_key])
                logger.debug(
                    "ticker_fuzzy_match",
                    org=org, org_stripped=org_stripped,
                    matched=original_key, score=score,
                )

        sentiment_score, sentiment_label = _compute_doc_sentiment(raw_entities)

        return ExtractionResult(
            tickers=list(dict.fromkeys(tickers)),
            context_orgs=list(dict.fromkeys(context_orgs)),
            sentiment_score=sentiment_score,
            sentiment_label=sentiment_label,
        )

    async def _fetch_entities(self, text: str) -> list[dict]:
        """Call Google NL API analyzeEntitySentiment.

        Returns list of raw entity dicts (all types, unfiltered) containing:
            name, type, salience, sentiment.score, sentiment.magnitude
        """
        payload = {
            "document": {"type": "PLAIN_TEXT", "content": text[:_MAX_TEXT_CHARS]},
            "encodingType": "UTF8",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    _NL_API_URL,
                    params={"key": self._api_key},
                    json=payload,
                )
                resp.raise_for_status()
                data: dict = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("nl_api_http_error", status=exc.response.status_code, text=exc.response.text[:200])
            return []
        except httpx.HTTPError as exc:
            logger.warning("nl_api_error", error=str(exc))
            return []

        return data.get("entities", [])


async def load_company_map(db_pool: object) -> dict[str, str]:
    """
    Build lookup dict for TickerExtractor from DB companies table.

    Indexes each company two ways so both resolve to the ticker:
      - company_name_lower → ticker  ("grameenphone plc" → "GP")
      - ticker_lower       → ticker  ("gp"               → "GP")

    The ticker key handles cases where articles use the DSE symbol directly
    (e.g. "GP shares rose 3%") without spelling out the full company name.
    """
    import asyncpg  # type: ignore[import]

    pool: asyncpg.Pool = db_pool  # type: ignore[assignment]
    rows = await pool.fetch("SELECT ticker, company_name FROM companies WHERE is_active = true")
    result: dict[str, str] = {}
    for row in rows:
        result[row["company_name"].lower()] = row["ticker"]
        result[row["ticker"].lower()] = row["ticker"]
    return result
