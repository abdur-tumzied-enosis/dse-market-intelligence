# chat/pdf_extractor.py
"""
Annual report PDF extraction via Gemini Files API.

Note: DSE/BSEC have no centralized PDF portal (confirmed Phase 1E).
This pipeline processes PDFs when URLs are scraped from per-company IR pages.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """\
Extract key financial metrics from this annual report PDF.
Return a JSON object with these fields (null if not found):
{
  "revenue": <number in BDT millions>,
  "net_income": <number in BDT millions>,
  "eps": <number>,
  "nav_per_share": <number>,
  "total_assets": <number in BDT millions>,
  "dividend_cash_pct": <number>,
  "dividend_stock_pct": <number>,
  "key_highlights": [<string>, ...]
}
JSON only, no explanation.\
"""

_CHUNK_SIZE = 800  # chars per document chunk for pgvector storage


def chunk_text(text: str, chunk_size: int = _CHUNK_SIZE, overlap: int = 80) -> list[str]:
    """Split text into overlapping chunks for embedding storage."""
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return [c for c in chunks if c.strip()]


class PdfExtractor:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self.api_key = api_key
        self.model = model

    async def extract_from_url(
        self, pdf_url: str, ticker: str, fiscal_year: int
    ) -> dict | None:
        """
        Download PDF, upload to Gemini Files API, extract metrics.
        Returns None if api_key is missing or extraction fails.
        """
        if not self.api_key:
            log.debug("pdf_extractor: no api_key, skip")
            return None

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = Path(tmp.name)
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.get(pdf_url)
                    resp.raise_for_status()
                    tmp_path.write_bytes(resp.content)

            return await asyncio.to_thread(self._extract_sync, tmp_path, ticker, fiscal_year)
        except Exception as exc:
            log.warning("pdf_extractor.failed ticker=%s year=%d: %s", ticker, fiscal_year, exc)
            return None
        finally:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def _extract_sync(self, pdf_path: Path, ticker: str, fiscal_year: int) -> dict | None:
        """Run in a thread (google-generativeai is sync)."""
        import google.generativeai as genai

        genai.configure(api_key=self.api_key)
        uploaded = genai.upload_file(path=str(pdf_path), mime_type="application/pdf")
        model = genai.GenerativeModel(self.model)
        response = model.generate_content([uploaded, _EXTRACTION_PROMPT])

        raw = response.text.strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                data["ticker"] = ticker
                data["fiscal_year"] = fiscal_year
                return data
            except json.JSONDecodeError:
                pass
        log.warning("pdf_extractor: failed to parse JSON for %s %d", ticker, fiscal_year)
        return None
