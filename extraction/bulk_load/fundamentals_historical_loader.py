"""
Historical fundamentals loader — scrapes displayCompany.php for all active tickers.

Extracts multi-year EPS, NAV, P/E, dividends (typically 5–8 years per company)
and writes into the fundamentals table with fiscal_year set.

Usage:
    python -m extraction.bulk_load.fundamentals_historical_loader              # all tickers
    python -m extraction.bulk_load.fundamentals_historical_loader BRACBANK GP  # specific

Rate: 3 concurrent, 1.5s delay → ~406 tickers in ~12 min.
"""
from __future__ import annotations

import asyncio
import logging
import math
import sys
from datetime import datetime, timezone
from typing import Any

from db.pool import get_pool
from extraction.adapters.dse_direct.company_info import CompanyBundle, DSEDirectCompanyInfoAdapter
from extraction.base import AdapterError

logger = logging.getLogger(__name__)

_CONCURRENCY = 3
_DELAY_S = 1.5


def _isnan(value: Any) -> bool:
    """Return True when value is a NaN float/numpy scalar; False otherwise."""
    try:
        return math.isnan(value)
    except (TypeError, ValueError):
        return False


def _num(value: Any) -> float | None:
    """Coerce pandas/numpy NaN to None so NUMERIC columns never store 'NaN'.

    Missing dividends/EPS arrive as None but pandas turns them into float NaN in
    the DataFrame; asyncpg writes that straight into NUMERIC as 'NaN', which then
    fails Pydantic finite_number validation on read.
    """
    if value is None:
        return None
    try:
        if math.isnan(value):
            return None
    except (TypeError, ValueError):
        return value
    return value


async def _write_bundle(pool: Any, bundle: CompanyBundle, job_id: str) -> dict[str, int]:
    """Write all five bundle datasets to the DB. Returns per-table upsert counts.

    Tables written:
        fundamentals           — one row per fiscal year (yearly)
        fundamentals_quarterly — one row per (ticker, fiscal_year, quarter)
        shareholding_history   — one row per (ticker, as_on_date)
        corporate_actions      — one row per (ticker, fiscal_year, action_type)
        companies              — meta columns updated via ON CONFLICT DO UPDATE

    mn→BDT: net_profit_bdt = net_profit_mn * 1e6;
            total_comprehensive_income_bdt = tci_mn * 1e6.
    Loans stay in mn units as the schema stores them that way.
    """
    counts = {"fundamentals": 0, "quarterly": 0, "shareholding": 0, "actions": 0, "company_meta": 0}

    # ── 1. fundamentals (yearly) ──────────────────────────────────────────
    for _, row in bundle.yearly.iterrows():
        np_mn_raw = row.get("net_profit_mn")
        tci_mn_raw = row.get("tci_mn")
        np_mn = float(np_mn_raw) if np_mn_raw is not None and not _isnan(np_mn_raw) else None
        tci_mn = float(tci_mn_raw) if tci_mn_raw is not None and not _isnan(tci_mn_raw) else None
        await pool.execute(
            """
            INSERT INTO fundamentals
                (ticker, fiscal_year,
                 eps, eps_basis, eps_diluted, nav,
                 net_profit_bdt, total_comprehensive_income_bdt,
                 pe, dividend_yield_pct,
                 cash_div_pct, stock_div_pct,
                 fetched_at, source, ingestion_job)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            ON CONFLICT (ticker, fiscal_year)
            WHERE fiscal_year IS NOT NULL
            DO UPDATE SET
                eps                             = EXCLUDED.eps,
                eps_basis                       = EXCLUDED.eps_basis,
                eps_diluted                     = EXCLUDED.eps_diluted,
                nav                             = EXCLUDED.nav,
                net_profit_bdt                  = EXCLUDED.net_profit_bdt,
                total_comprehensive_income_bdt  = EXCLUDED.total_comprehensive_income_bdt,
                pe                              = EXCLUDED.pe,
                dividend_yield_pct              = EXCLUDED.dividend_yield_pct,
                cash_div_pct                    = EXCLUDED.cash_div_pct,
                stock_div_pct                   = EXCLUDED.stock_div_pct,
                fetched_at                      = EXCLUDED.fetched_at,
                ingestion_job                   = EXCLUDED.ingestion_job
            """,
            row["ticker"],
            int(row["fiscal_year"]),
            _num(row.get("eps")),
            row.get("eps_basis") if row.get("eps_basis") else None,
            _num(row.get("eps_diluted")),
            _num(row.get("nav")),
            (np_mn * 1e6) if np_mn is not None else None,
            (tci_mn * 1e6) if tci_mn is not None else None,
            _num(row.get("pe")),
            _num(row.get("dividend_yield")),
            _num(row.get("cash_div_pct")),
            _num(row.get("stock_div_pct")),
            row["fetched_at"],
            row["source"],
            job_id,
        )
        counts["fundamentals"] += 1

    # ── 2. fundamentals_quarterly ─────────────────────────────────────────
    for _, row in bundle.quarterly.iterrows():
        await pool.execute(
            """
            INSERT INTO fundamentals_quarterly
                (ticker, fiscal_year, quarter,
                 eps_basic, eps_diluted, period_end_price,
                 fetched_at, source)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (ticker, fiscal_year, quarter)
            DO UPDATE SET
                eps_basic        = EXCLUDED.eps_basic,
                eps_diluted      = EXCLUDED.eps_diluted,
                period_end_price = EXCLUDED.period_end_price,
                fetched_at       = EXCLUDED.fetched_at
            """,
            row["ticker"],
            int(row["fiscal_year"]),
            int(row["quarter"]),
            _num(row.get("eps_basic")),
            _num(row.get("eps_diluted")),
            _num(row.get("period_end_price")),
            row["fetched_at"],
            row["source"],
        )
        counts["quarterly"] += 1

    # ── 3. shareholding_history ───────────────────────────────────────────
    for _, row in bundle.shareholding.iterrows():
        as_on = row.get("as_on_date")
        if as_on is None:
            # Skip rows with no date — UNIQUE constraint requires as_on_date NOT NULL
            continue
        await pool.execute(
            """
            INSERT INTO shareholding_history
                (ticker, as_on_date,
                 sponsor_pct, govt_pct, institution_pct, foreign_pct, public_pct,
                 fetched_at, source)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (ticker, as_on_date)
            DO UPDATE SET
                sponsor_pct     = EXCLUDED.sponsor_pct,
                govt_pct        = EXCLUDED.govt_pct,
                institution_pct = EXCLUDED.institution_pct,
                foreign_pct     = EXCLUDED.foreign_pct,
                public_pct      = EXCLUDED.public_pct,
                fetched_at      = EXCLUDED.fetched_at
            """,
            row["ticker"],
            as_on,
            _num(row.get("sponsor_pct")),
            _num(row.get("govt_pct")),
            _num(row.get("institution_pct")),
            _num(row.get("foreign_pct")),
            _num(row.get("public_pct")),
            row["fetched_at"],
            row["source"],
        )
        counts["shareholding"] += 1

    # ── 4. corporate_actions ──────────────────────────────────────────────
    for _, row in bundle.actions.iterrows():
        await pool.execute(
            """
            INSERT INTO corporate_actions
                (ticker, fiscal_year, action_type,
                 value_pct, ratio_text, ratio, source)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (ticker, fiscal_year, action_type)
            DO UPDATE SET
                value_pct  = EXCLUDED.value_pct,
                ratio_text = EXCLUDED.ratio_text,
                ratio      = EXCLUDED.ratio
            """,
            row["ticker"],
            int(row["fiscal_year"]),
            row["action_type"],
            _num(row.get("value_pct")),
            row.get("ratio_text") if row.get("ratio_text") else None,
            _num(row.get("ratio")),
            row["source"],
        )
        counts["actions"] += 1

    # ── 5. companies meta (latest-snapshot semantics) ─────────────────────
    meta = bundle.company_meta
    if meta:
        ticker = meta["ticker"]
        # debut_trading_date: the page prints strings like "20-Jan-2009" or
        # "Jan 20, 2009" — parse directly; fall back to None when unparseable.
        from datetime import date as _date
        ddt = meta.get("debut_trading_date")
        if isinstance(ddt, str):
            from datetime import datetime as _dt
            ddt_parsed = None
            for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%b %d, %Y"):
                try:
                    ddt_parsed = _dt.strptime(ddt.strip(), fmt).date()
                    break
                except ValueError:
                    continue
            ddt = ddt_parsed
        elif not isinstance(ddt, _date):
            ddt = None

        # COALESCE = transiently blank page section must not wipe stored values;
        # consequence: a removed value can't clear itself (accepted, design §3.5).
        await pool.execute(
            """
            UPDATE companies SET
                face_value         = COALESCE($2,  face_value),
                market_lot         = COALESCE($3,  market_lot),
                scrip_code         = COALESCE($4,  scrip_code),
                electronic_share   = COALESCE($5,  electronic_share),
                debut_trading_date = COALESCE($6,  debut_trading_date),
                operational_status = COALESCE($7,  operational_status),
                short_loan_mn      = COALESCE($8,  short_loan_mn),
                long_loan_mn       = COALESCE($9,  long_loan_mn),
                loan_as_on         = COALESCE($10, loan_as_on),
                credit_rating_st   = COALESCE($11, credit_rating_st),
                credit_rating_lt   = COALESCE($12, credit_rating_lt),
                delisting_remark   = COALESCE($13, delisting_remark),
                ir_url             = COALESCE($14, ir_url),
                psi_url            = COALESCE($15, psi_url)
            WHERE ticker = $1
            """,
            ticker,
            _num(float(meta["face_value"])) if meta.get("face_value") is not None else None,
            int(meta["market_lot"]) if meta.get("market_lot") is not None else None,
            meta.get("scrip_code"),
            meta.get("electronic_share"),
            ddt,
            meta.get("operational_status"),
            _num(meta.get("short_loan_mn")),
            _num(meta.get("long_loan_mn")),
            meta.get("loan_as_on"),
            meta.get("credit_rating_st"),
            meta.get("credit_rating_lt"),
            meta.get("delisting_remark"),
            meta.get("ir_url"),
            meta.get("psi_url"),
        )
        counts["company_meta"] += 1

    return counts


async def _load_one(
    pool,
    ticker: str,
    adapter: DSEDirectCompanyInfoAdapter,
    job_id: str,
) -> dict:
    try:
        bundle = await adapter.fetch_company_bundle(ticker=ticker)
    except AdapterError as exc:
        logger.warning("fundamentals_hist_failed ticker=%s error=%s", ticker, exc)
        return {"ticker": ticker, "status": "failed", "upserted": 0,
                "quarterly": 0, "shareholding": 0, "actions": 0}

    counts = await _write_bundle(pool, bundle, job_id)
    return {
        "ticker": ticker,
        "status": "ok",
        "upserted": counts["fundamentals"],
        "years": len(bundle.yearly),
        "quarterly": counts["quarterly"],
        "shareholding": counts["shareholding"],
        "actions": counts["actions"],
    }


async def bulk_load_fundamentals_historical(tickers: list[str] | None = None) -> dict:
    pool = await get_pool()

    if tickers is None:
        rows = await pool.fetch(
            "SELECT ticker FROM companies WHERE is_active = true ORDER BY ticker"
        )
        tickers = [r["ticker"] for r in rows]

    total = len(tickers)
    logger.info("fundamentals_hist_start total=%d", total)

    adapter = DSEDirectCompanyInfoAdapter()
    sem = asyncio.Semaphore(_CONCURRENCY)
    job_id = f"fundamentals_hist_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    done = 0
    summary: dict = {
        "ok": 0, "failed": 0,
        "total_upserted": 0, "quarterly": 0, "shareholding": 0, "actions": 0,
    }

    async def fetch_one(ticker: str) -> None:
        nonlocal done
        async with sem:
            r = await _load_one(pool, ticker, adapter, job_id)
            done += 1
            if r["status"] == "ok":
                summary["ok"] += 1
                summary["total_upserted"] += r["upserted"]
                summary["quarterly"] += r["quarterly"]
                summary["shareholding"] += r["shareholding"]
                summary["actions"] += r["actions"]
                logger.info(
                    "[%d/%d] %s years=%d upserted=%d quarterly=%d",
                    done, total, ticker, r["years"], r["upserted"], r["quarterly"],
                )
            else:
                summary["failed"] += 1
                logger.warning("[%d/%d] %s FAILED", done, total, ticker)
            await asyncio.sleep(_DELAY_S)

    await asyncio.gather(*[fetch_one(t) for t in tickers])
    logger.info(
        "fundamentals_hist_done ok=%d failed=%d upserted=%d quarterly=%d shareholding=%d actions=%d",
        summary["ok"], summary["failed"], summary["total_upserted"],
        summary["quarterly"], summary["shareholding"], summary["actions"],
    )
    return summary


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(levelname)s %(message)s")
    tickers_arg = sys.argv[1:] or None
    asyncio.run(bulk_load_fundamentals_historical(tickers_arg))
