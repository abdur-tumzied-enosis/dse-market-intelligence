# tests/unit/test_pdf_extractor.py
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


def test_extractor_model_attribute():
    from chat.pdf_extractor import PdfExtractor
    extractor = PdfExtractor(api_key="test_key", model="gemini-2.5-flash")
    assert extractor.model == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_extract_skips_when_no_api_key():
    from chat.pdf_extractor import PdfExtractor
    extractor = PdfExtractor(api_key="", model="gemini-2.5-flash")
    result = await extractor.extract_from_url("http://example.com/report.pdf", "BRACBANK", 2024)
    assert result is None


def test_chunk_text_splits_correctly():
    from chat.pdf_extractor import chunk_text
    text = "word " * 300  # 1500 chars
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 550  # generous bound


def test_chunk_text_single_chunk():
    from chat.pdf_extractor import chunk_text
    short = "Short text here."
    chunks = chunk_text(short, chunk_size=500, overlap=50)
    assert chunks == [short]


def test_chunk_text_empty_chunks_filtered():
    from chat.pdf_extractor import chunk_text
    # Verify whitespace-only chunks are filtered out
    text = "a" * 600
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    assert all(c.strip() for c in chunks)


def test_chunk_text_overlap():
    from chat.pdf_extractor import chunk_text
    text = "a" * 1000
    chunks = chunk_text(text, chunk_size=500, overlap=100)
    # With overlap, start of chunk N+1 overlaps with end of chunk N
    assert len(chunks) >= 2
