"""Unit tests for announcement_parser — pure unit tests, no fixtures needed."""
from __future__ import annotations

import pytest

from extraction.parsers.announcement_parser import (
    ParsedAnnouncement,
    content_hash,
    parse_announcement,
)


# ---------------------------------------------------------------------------
# _classify_type
# ---------------------------------------------------------------------------

class TestClassifyType:
    @pytest.fixture(autouse=True)
    def _fn(self):
        from extraction.parsers.announcement_parser import _classify_type
        self.fn = _classify_type

    def test_eps_disclosure(self):
        assert self.fn("EPS of Tk. 5.20 for Q1 2026") == "eps_disclosure"

    def test_earnings_per_share_headline(self):
        assert self.fn("Earnings Per Share Q2 2025") == "eps_disclosure"

    def test_dividend(self):
        assert self.fn("Recommendation of Cash Dividend") == "dividend"

    def test_agm(self):
        assert self.fn("Notice of Annual General Meeting") == "agm"

    def test_board_meeting(self):
        assert self.fn("Notice of Board Meeting") == "board_meeting"

    def test_rights_issue(self):
        assert self.fn("Rights Issue Announcement") == "rights_issue"

    def test_suspension(self):
        assert self.fn("Suspension of Trading") == "suspension"

    def test_resumption(self):
        assert self.fn("Resumption of Trading") == "resumption"

    def test_auditor_qualification(self):
        assert self.fn("Emphasis of Matter in Audit Report") == "auditor_qualification"

    def test_going_concern(self):
        assert self.fn("Going Concern Doubt Raised by Auditor") == "auditor_qualification"

    def test_ipo(self):
        assert self.fn("Initial Public Offering (IPO) Notice") == "ipo"

    def test_credit_rating(self):
        assert self.fn("Credit Rating Updated") == "credit_rating"

    def test_other(self):
        assert self.fn("Change of Company Secretary") == "other"

    def test_earnings_disclosure_event_is_other(self):
        # "Earnings Disclosure" = investor webinar event, NOT actual EPS — must be "other"
        result = self.fn("Event on Earnings Disclosure Q1 2026")
        assert result == "other"

    def test_case_insensitive(self):
        assert self.fn("DIVIDEND ANNOUNCEMENT") == "dividend"


# ---------------------------------------------------------------------------
# _extract_eps
# ---------------------------------------------------------------------------

class TestExtractEPS:
    @pytest.fixture(autouse=True)
    def _fn(self):
        from extraction.parsers.announcement_parser import _extract_eps
        self.fn = _extract_eps

    def test_eps_bdt_value(self):
        value, period, eps_type = self.fn("EPS of BDT 5.20 for Q1 2026")
        assert value == 5.20

    def test_eps_tk_value(self):
        value, _, _ = self.fn("EPS Tk. 3.50 for Q2 2025")
        assert value == 3.50

    def test_quarter_period(self):
        _, period, _ = self.fn("EPS Tk 2.10 for Q3 2025")
        assert period == "Q3_2025"

    def test_half_year_period(self):
        _, period, _ = self.fn("EPS Tk 4.50 for H1 2025")
        assert period == "H1_2025"

    def test_fy_period(self):
        _, period, _ = self.fn("EPS BDT 21.90 for FY2025")
        assert period == "FY2025"

    def test_year_ended_period(self):
        _, period, _ = self.fn("EPS Tk 6.95 for the year ended December 2024")
        assert period == "FY2024"

    def test_month_range_q1(self):
        _, period, _ = self.fn("EPS Tk 3.33 for January to March 2024")
        assert period == "Q1_2024"

    def test_month_range_h2(self):
        _, period, _ = self.fn("EPS Tk 5.00 for July to December 2024")
        assert period == "H2_2024"

    def test_audited_type(self):
        _, _, eps_type = self.fn("Audited EPS Tk 8.50 for FY2024")
        assert eps_type == "audited"

    def test_unaudited_type(self):
        _, _, eps_type = self.fn("Unaudited EPS Tk 4.20 for Q1 2026")
        assert eps_type == "unaudited"

    def test_no_type_returns_none(self):
        _, _, eps_type = self.fn("EPS Tk 3.00 for Q2 2025")
        assert eps_type is None

    def test_no_eps_keyword_returns_none_value(self):
        value, _, _ = self.fn("Cash dividend 15% for FY2024")
        assert value is None

    def test_no_period_returns_none(self):
        _, period, _ = self.fn("EPS Tk 5.00")
        assert period is None


# ---------------------------------------------------------------------------
# _extract_dividend
# ---------------------------------------------------------------------------

class TestExtractDividend:
    @pytest.fixture(autouse=True)
    def _fn(self):
        from extraction.parsers.announcement_parser import _extract_dividend
        self.fn = _extract_dividend

    def test_cash_dividend_pct(self):
        cash, _, _ = self.fn("Cash Dividend @12.50%", "")
        assert cash == 12.5

    def test_cash_dividend_keyword(self):
        cash, _, _ = self.fn("", "Cash Dividend 15%")
        assert cash == 15.0

    def test_stock_dividend_pct(self):
        _, stock, _ = self.fn("Stock Dividend @10%", "")
        assert stock == 10.0

    def test_bonus_share(self):
        _, stock, _ = self.fn("", "Bonus Shares @5%")
        assert stock == 5.0

    def test_year_extracted(self):
        _, _, year = self.fn("Dividend for the year 2024", "")
        assert year == 2024

    def test_fy_year(self):
        _, _, year = self.fn("", "Cash Dividend 12.50% for FY 2025")
        assert year == 2025

    def test_combined_headline_details(self):
        cash, stock, year = self.fn(
            "Board recommends Cash Dividend @15% and Stock Dividend @10%",
            "for the year ended December 2024"
        )
        assert cash == 15.0
        assert stock == 10.0
        assert year == 2024

    def test_no_dividend_returns_none(self):
        cash, stock, year = self.fn("AGM Notice", "Meeting on 30 June 2026")
        assert cash is None
        assert stock is None


# ---------------------------------------------------------------------------
# content_hash
# ---------------------------------------------------------------------------

class TestContentHash:
    def test_deterministic(self):
        h1 = content_hash("GP", "AGM Notice", "2026-05-01")
        h2 = content_hash("GP", "AGM Notice", "2026-05-01")
        assert h1 == h2

    def test_different_inputs_different_hash(self):
        h1 = content_hash("GP", "AGM Notice", "2026-05-01")
        h2 = content_hash("GP", "AGM Notice", "2026-05-02")
        assert h1 != h2

    def test_returns_string(self):
        assert isinstance(content_hash("GP", "Test", "2026-01-01"), str)

    def test_length_32(self):
        # MD5 hex = 32 chars
        assert len(content_hash("GP", "Test", "2026-01-01")) == 32

    def test_ticker_matters(self):
        h1 = content_hash("GP", "Dividend", "2026-01-01")
        h2 = content_hash("BRACBANK", "Dividend", "2026-01-01")
        assert h1 != h2


# ---------------------------------------------------------------------------
# parse_announcement integration
# ---------------------------------------------------------------------------

class TestParseAnnouncement:
    def test_eps_disclosure_with_value(self):
        result = parse_announcement(
            ticker="BRACBANK",
            headline="Unaudited EPS for Q1 FY2026",
            details="The company has reported Unaudited EPS of BDT 1.45 for Q1 FY2026 (January-March 2026).",
            published_at="2026-04-30",
        )
        assert result.announcement_type == "eps_disclosure"
        assert result.eps_value == 1.45
        assert result.eps_type == "unaudited"

    def test_dividend_with_cash_and_stock(self):
        result = parse_announcement(
            ticker="BRACBANK",
            headline="Recommendation of Dividend",
            details="The Board recommends Cash Dividend @12.50% and Stock Dividend @12.50% for the year 2024.",
            published_at="2025-01-15",
        )
        assert result.announcement_type == "dividend"
        assert result.dividend_cash_pct == 12.5
        assert result.dividend_stock_pct == 12.5
        assert result.dividend_year == 2024

    def test_dividend_continuation_with_eps(self):
        # DSE continuation news: dividend type but contains EPS data
        result = parse_announcement(
            ticker="GP",
            headline="(Continuation news of GP)",
            details="The company reported EPS of Tk. 21.90 for FY2025.",
            published_at="2025-08-01",
        )
        assert result.eps_value == 21.90
        assert result.eps_period == "FY2025"

    def test_agm_classified_correctly(self):
        result = parse_announcement(
            ticker="CITYBANK",
            headline="Notice of Annual General Meeting",
            details="AGM scheduled for 30 June 2026 at 10:00 AM.",
            published_at="2026-05-01",
        )
        assert result.announcement_type == "agm"
        assert result.eps_value is None

    def test_board_meeting(self):
        result = parse_announcement(
            ticker="EBL",
            headline="Disclosure for Board Meeting",
            details="Board meeting scheduled to review quarterly results.",
            published_at="2026-04-01",
        )
        assert result.announcement_type == "board_meeting"

    def test_suspension(self):
        result = parse_announcement(
            ticker="SOME",
            headline="Suspension of Trading",
            details="Trading in SOME shares suspended effective immediately.",
            published_at="2026-03-01",
        )
        assert result.announcement_type == "suspension"

    def test_other_type(self):
        result = parse_announcement(
            ticker="GP",
            headline="Change of Registered Address",
            details="The company has changed its registered address.",
            published_at="2026-01-01",
        )
        assert result.announcement_type == "other"
        assert result.eps_value is None
        assert result.dividend_cash_pct is None

    def test_returns_parsed_announcement_type(self):
        result = parse_announcement("GP", "Some news", "", "2026-01-01")
        assert isinstance(result, ParsedAnnouncement)

    def test_earnings_disclosure_event_no_eps_type(self):
        # "Event on Earnings Disclosure" should NOT be classified as eps_disclosure
        result = parse_announcement(
            ticker="BRACBANK",
            headline="Event on Earnings Disclosure for Q1 FY2026",
            details="An investor event to discuss Q1 FY2026 results will be held on May 5, 2026.",
            published_at="2026-04-20",
        )
        assert result.announcement_type == "other"
