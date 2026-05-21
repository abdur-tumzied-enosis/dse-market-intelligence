"""Unit tests for extraction/normalizers.py — no network calls."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from extraction.normalizers import (
    DHAKA_TZ,
    bd_date_str_to_utc,
    normalize_ticker,
    to_decimal,
    to_utc,
)


class TestToDecimal:
    def test_string_bdt(self):
        assert to_decimal("1,234.56") == Decimal("1234.56")

    def test_string_plain(self):
        assert to_decimal("42.5") == Decimal("42.5")

    def test_integer(self):
        assert to_decimal(100) == Decimal("100")

    def test_float(self):
        assert to_decimal(3.14) == Decimal("3.14")

    def test_none(self):
        assert to_decimal(None) is None

    def test_empty_string(self):
        assert to_decimal("") is None

    def test_dash(self):
        assert to_decimal("-") is None

    def test_negative(self):
        assert to_decimal("-5.2") == Decimal("-5.2")

    def test_already_decimal(self):
        d = Decimal("99.99")
        assert to_decimal(d) == d


class TestToUtc:
    def test_naive_assumed_dhaka(self):
        naive = datetime(2026, 5, 21, 10, 0, 0)
        result = to_utc(naive, assume_dhaka=True)
        assert result.tzinfo == timezone.utc
        # Dhaka is UTC+6 — 10:00 Dhaka = 04:00 UTC
        assert result.hour == 4

    def test_naive_assumed_utc(self):
        naive = datetime(2026, 5, 21, 10, 0, 0)
        result = to_utc(naive, assume_dhaka=False)
        assert result == datetime(2026, 5, 21, 10, 0, 0, tzinfo=timezone.utc)

    def test_already_utc(self):
        aware = datetime(2026, 5, 21, 4, 0, 0, tzinfo=timezone.utc)
        assert to_utc(aware) == aware

    def test_already_dhaka(self):
        aware = datetime(2026, 5, 21, 10, 0, 0, tzinfo=DHAKA_TZ)
        result = to_utc(aware)
        assert result.tzinfo == timezone.utc
        assert result.hour == 4

    def test_none(self):
        assert to_utc(None) is None


class TestNormalizeTicker:
    def test_lowercase(self):
        assert normalize_ticker("squrpharma") == "SQURPHARMA"

    def test_whitespace(self):
        assert normalize_ticker("  GP  ") == "GP"

    def test_already_upper(self):
        assert normalize_ticker("BRACBANK") == "BRACBANK"


class TestBdDateStrToUtc:
    def test_valid_date(self):
        result = bd_date_str_to_utc("21-May-2026")
        assert result is not None
        assert result.tzinfo == timezone.utc
        # Midnight Dhaka = 18:00 UTC previous day
        assert result.day in (20, 21)

    def test_invalid_date(self):
        assert bd_date_str_to_utc("not-a-date") is None

    def test_none_like(self):
        assert bd_date_str_to_utc("") is None
