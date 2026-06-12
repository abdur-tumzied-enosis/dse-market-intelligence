"""Unit tests for fundamentals_historical_loader._write_bundle (Task 8).

These tests use a FakePool (no DB required) to verify:
  1. All five tables are touched on a normal bundle write.
  2. mn→BDT conversion: net_profit_bdt = net_profit_mn * 1e6 (no double-conversion).
  3. Idempotent re-run: every INSERT uses ON CONFLICT DO UPDATE.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest

from extraction.adapters.dse_direct.company_info import CompanyBundle

# ---------------------------------------------------------------------------
# FakePool — records every SQL statement executed
# ---------------------------------------------------------------------------

class FakePool:
    """Minimal asyncpg-compatible fake pool that records execute() calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append((sql, args))
        return "INSERT 0 1"

    def sqls(self) -> list[str]:
        """Return lower-cased SQL strings for easy assertion."""
        return [sql.lower() for sql, _ in self.calls]

    def tables_touched(self) -> set[str]:
        """Set of table names that appear in INSERT INTO or UPDATE statements."""
        out: set[str] = set()
        for sql in self.sqls():
            if "insert into" in sql:
                after = sql.split("insert into", 1)[1].strip()
                table = after.split()[0].strip("(")
                out.add(table)
            elif sql.lstrip().startswith("update "):
                after = sql.lstrip()[len("update "):].strip()
                table = after.split()[0].strip()
                out.add(table)
        return out

    def all_have_on_conflict(self) -> bool:
        """Every INSERT must include ON CONFLICT."""
        for sql in self.sqls():
            if "insert into" in sql and "on conflict" not in sql:
                return False
        return True


# ---------------------------------------------------------------------------
# Minimal bundle factory
# ---------------------------------------------------------------------------

def _make_bundle(ticker: str = "TESTCO") -> CompanyBundle:
    now = datetime.now(UTC)

    yearly = pd.DataFrame([
        {
            "ticker": ticker, "fiscal_year": 2024, "eps": Decimal("5.12"),
            "eps_basis": "basic_restated", "eps_diluted": Decimal("5.10"),
            "nav": Decimal("45.0"), "net_profit_mn": Decimal("1234.56"),
            "tci_mn": Decimal("1300.00"),
            "pe": Decimal("12.5"), "dividend_yield": Decimal("3.2"),
            "cash_div_pct": Decimal("15.0"), "stock_div_pct": None,
            "fetched_at": now, "source": "dse_direct_company_info",
        },
    ])

    quarterly = pd.DataFrame([
        {
            "ticker": ticker, "fiscal_year": 2024, "quarter": 1,
            "eps_basic": Decimal("1.20"), "eps_diluted": Decimal("1.18"),
            "period_end_price": Decimal("62.3"),
            "fetched_at": now, "source": "dse_direct_company_info",
        },
    ])

    shareholding = pd.DataFrame([
        {
            "ticker": ticker, "as_on_date": date(2024, 12, 31),
            "sponsor_pct": Decimal("30.0"), "govt_pct": Decimal("0.0"),
            "institution_pct": Decimal("25.0"), "foreign_pct": Decimal("5.0"),
            "public_pct": Decimal("40.0"),
            "fetched_at": now, "source": "dse_direct_company_info",
        },
    ])

    actions = pd.DataFrame([
        {
            "ticker": ticker, "fiscal_year": 2024,
            "action_type": "cash_div", "value_pct": Decimal("15.0"),
            "ratio_text": None, "ratio": None,
            "source": "dse_direct_company_info",
        },
    ])

    company_meta: dict[str, Any] = {
        "ticker": ticker,
        "face_value": Decimal("10.0"),
        "market_lot": 1,
        "debut_trading_date": "20-Jan-2009",
        "scrip_code": "11102",
        "electronic_share": True,
        "operational_status": "Active",
        "short_loan_mn": None,
        "long_loan_mn": Decimal("500.0"),
        "loan_as_on": date(2024, 12, 31),
        "credit_rating_st": "ST-2",
        "credit_rating_lt": "AA",
        "delisting_remark": None,
        "ir_url": "https://example.com/ir",
        "psi_url": None,
    }

    return CompanyBundle(
        yearly=yearly,
        quarterly=quarterly,
        shareholding=shareholding,
        actions=actions,
        company_meta=company_meta,
    )


# ---------------------------------------------------------------------------
# Test 1: all five tables are touched
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_bundle_touches_all_tables():
    """_write_bundle must INSERT into fundamentals, fundamentals_quarterly,
    shareholding_history, corporate_actions, and companies."""
    from extraction.bulk_load.fundamentals_historical_loader import _write_bundle

    pool = FakePool()
    bundle = _make_bundle()
    counts = await _write_bundle(pool, bundle, job_id="test_job_001")

    touched = pool.tables_touched()
    assert "fundamentals" in touched, f"fundamentals missing from {touched}"
    assert "fundamentals_quarterly" in touched, f"fundamentals_quarterly missing from {touched}"
    assert "shareholding_history" in touched, f"shareholding_history missing from {touched}"
    assert "corporate_actions" in touched, f"corporate_actions missing from {touched}"
    assert "companies" in touched, f"companies missing from {touched}"

    # returned counts must be non-negative ints
    for key in ("fundamentals", "quarterly", "shareholding", "actions", "company_meta"):
        assert counts[key] >= 0, f"counts[{key!r}] is negative"


# ---------------------------------------------------------------------------
# Test 2: mn→BDT conversion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_bundle_mn_to_bdt_conversion():
    """net_profit_bdt stored = net_profit_mn * 1e6; no double-conversion on re-run."""
    from extraction.bulk_load.fundamentals_historical_loader import _write_bundle

    pool = FakePool()
    bundle = _make_bundle()
    # net_profit_mn = 1234.56 in the bundle
    await _write_bundle(pool, bundle, job_id="test_job_002")

    # Find the fundamentals INSERT and locate the net_profit_bdt argument
    fund_calls = [
        (sql, args) for sql, args in pool.calls
        if "insert into fundamentals" in sql.lower()
        and "fundamentals_quarterly" not in sql.lower()
    ]
    assert fund_calls, "no fundamentals INSERT found"
    sql, args = fund_calls[0]

    # net_profit_bdt = 1234.56 * 1e6 = 1_234_560_000.0
    expected_bdt = float(Decimal("1234.56")) * 1e6

    # Find the value in args — it may be float or Decimal
    bdt_values = [
        float(a) for a in args
        if a is not None and not isinstance(a, (str, bool, datetime, date))
        and abs(float(a) - expected_bdt) < 1.0
    ]
    assert bdt_values, (
        f"expected net_profit_bdt≈{expected_bdt} in args; got {args}"
    )

    # Guard: value should NOT equal the mn value (no-double-conversion)
    mn_val = float(Decimal("1234.56"))
    assert not any(
        abs(float(a) - mn_val) < 0.01
        for a in args
        if a is not None and not isinstance(a, (str, bool, datetime, date))
    ), "net_profit_mn stored raw — missing ×1e6 conversion"


# ---------------------------------------------------------------------------
# Test 3: idempotent re-run — every INSERT has ON CONFLICT DO UPDATE
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_bundle_idempotent_on_conflict():
    """Every INSERT statement must contain ON CONFLICT so re-runs are safe."""
    from extraction.bulk_load.fundamentals_historical_loader import _write_bundle

    pool = FakePool()
    bundle = _make_bundle()

    # Run twice (simulates a re-run / re-load)
    await _write_bundle(pool, bundle, job_id="test_job_003a")
    await _write_bundle(pool, bundle, job_id="test_job_003b")

    assert pool.all_have_on_conflict(), (
        "Some INSERT statements are missing ON CONFLICT DO UPDATE:\n"
        + "\n".join(sql for sql in pool.sqls() if "insert" in sql and "on conflict" not in sql)
    )
