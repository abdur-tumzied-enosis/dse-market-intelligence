"""Point-in-time predictive validation for the cross-sectional momentum scorer.

Answers the only question that matters before tuning weights: does
`compute_momentum_scores` actually rank tomorrow's winners above tomorrow's
losers on the DSE, or is it noise?

Method (no look-ahead, survivorship-aware):
  * Load the full daily close panel for EVERY ticker that ever had prices —
    including delisted/inactive names — so the backtest universe is not just
    today's survivors.
  * At each monthly rebalance date D, truncate every series to bars ≤ D and
    recompute scores cross-sectionally on past-only data (exactly what the live
    job sees on day D).
  * Forward return = each ticker's own H trading bars after D (entry = last bar
    ≤ D, exit = +H bars), so illiquid non-trading days don't create NaNs.
  * Score quality = rank-IC (Spearman of score vs forward return) per date, plus
    a top-minus-bottom decile spread. Compared against a canonical 12-1 momentum
    baseline to see whether the 5-signal composite adds anything.

Caveat: monthly rebalancing with horizons > 21 trading days produces
OVERLAPPING forward windows, so the IC t-stat is anti-conservative (effective
sample < number of dates). Treat t-stats as directional, not exact.

Run: python -m ml.eval.momentum_validation
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

import numpy as np
import pandas as pd

from db.pool import get_batch_pool
from ml.eval.metrics import rank_ic
from ml.scoring.momentum_score import compute_momentum_scores

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

_LOOKBACK_DAYS = 1825          # ~5 years of history to back-test over
_REBALANCE_STEP = 21           # rebalance every ~month (trading bars of master calendar)
_HORIZONS = (21, 63)           # forward holding windows in trading bars (1m, 3m)
_MIN_SCORE_BARS = 70           # mirror momentum_score._MIN_BARS
_BASELINE_BARS = 252           # 12-1 momentum lookback
_BASELINE_SKIP = 21            # skip most-recent month (short-term reversal)
_TURNOVER_WINDOW = 63          # trailing bars for the liquidity (turnover) measure
# Liquidity buckets: keep names whose trailing-turnover percentile (within the
# date's scored universe) is >= cutoff. "all"=full, "top50"=most-liquid half, etc.
_LIQ_BUCKETS = {"all": 0.0, "top50": 0.50, "top25": 0.75}


async def load_price_panel(
    pool, *, active_only: bool = False
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    """Per-ticker ascending daily (close, turnover) series.

    turnover = daily traded value in BDT (COALESCE(value_bdt, close*volume)).
    active_only=False (default) INCLUDES delisted/inactive tickers so the
    backtest is not survivorship-biased.
    """
    where_active = "AND c.is_active = true" if active_only else ""
    rows = await pool.fetch(
        f"""
        SELECT sp.ticker,
               sp.time::date AS day,
               (array_agg(sp.close ORDER BY sp.time DESC))[1] AS close,
               SUM(COALESCE(sp.value_bdt, sp.close * sp.volume))::float8 AS turnover
        FROM stock_prices sp
        JOIN companies c ON c.ticker = sp.ticker {where_active}
        WHERE sp.time >= NOW() - ($1 || ' days')::interval
          AND sp.close IS NOT NULL AND sp.close > 0
        GROUP BY sp.ticker, sp.time::date
        ORDER BY sp.ticker, day
        """,
        str(_LOOKBACK_DAYS),
    )
    close_by: dict[str, list[tuple[object, float]]] = defaultdict(list)
    turn_by: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        close_by[r["ticker"]].append((r["day"], float(r["close"])))
        turn_by[r["ticker"]].append(float(r["turnover"]) if r["turnover"] is not None else np.nan)
    closes = {
        t: pd.Series([c for _, c in pts], index=pd.to_datetime([d for d, _ in pts]))
        for t, pts in close_by.items()
    }
    turns = {
        t: pd.Series(turn_by[t], index=closes[t].index) for t in closes
    }
    return closes, turns


def _master_calendar(panel: dict[str, pd.Series]) -> pd.DatetimeIndex:
    """Sorted union of every trading day seen across the panel."""
    all_days = pd.DatetimeIndex([])
    for s in panel.values():
        all_days = all_days.union(s.index)
    return all_days.sort_values()


def _baseline_1212(series: pd.Series, pos: int) -> float:
    """Canonical 12-1 momentum: return over the year ending one month ago."""
    end = pos - _BASELINE_SKIP
    start = end - (_BASELINE_BARS - _BASELINE_SKIP)
    if start < 0 or end <= start:
        return np.nan
    return float(series.iloc[end] / series.iloc[start] - 1.0)


def evaluate(
    closes: dict[str, pd.Series], turns: dict[str, pd.Series]
) -> dict:
    """Run the point-in-time validation across liquidity buckets. Returns a report dict."""
    calendar = _master_calendar(closes)
    max_h = max(_HORIZONS)
    # leave history room at the front, forward room at the back
    rebal = calendar[_BASELINE_BARS : len(calendar) - max_h : _REBALANCE_STEP]
    log.info("calendar=%d days, rebalance dates=%d", len(calendar), len(rebal))

    # ic_score[bucket][h] = [per-date IC]; decile_rows[bucket][h] = [per-date frames]
    ic_score: dict[str, dict[int, list[float]]] = {b: {h: [] for h in _HORIZONS} for b in _LIQ_BUCKETS}
    ic_base: dict[str, dict[int, list[float]]] = {b: {h: [] for h in _HORIZONS} for b in _LIQ_BUCKETS}
    decile_rows: dict[str, dict[int, list[pd.DataFrame]]] = {b: {h: [] for h in _HORIZONS} for b in _LIQ_BUCKETS}
    coverage: list[int] = []

    for d in rebal:
        # 1) truncate every series to bars <= d; remember each ticker's pos
        trunc: dict[str, pd.Series] = {}
        pos_of: dict[str, int] = {}
        for ticker, s in closes.items():
            pos = int(s.index.searchsorted(d, side="right")) - 1
            if pos + 1 < _MIN_SCORE_BARS:
                continue
            trunc[ticker] = s.iloc[: pos + 1]
            pos_of[ticker] = pos
        if len(trunc) < 10:
            continue

        # 2) point-in-time scores (cross-sectional, past-only)
        scores = compute_momentum_scores(trunc)
        coverage.append(len(scores))

        # 3) build per-horizon row set once, then slice into liquidity buckets
        for h in _HORIZONS:
            recs: list[tuple[float, float, float, float]] = []
            for ticker, score in scores.items():
                s = closes[ticker]
                pos = pos_of[ticker]
                if pos + h >= len(s):
                    continue
                entry = float(s.iloc[pos])
                if entry <= 0:
                    continue
                fwd = float(s.iloc[pos + h]) / entry - 1.0
                base = _baseline_1212(s, pos)
                trail = turns[ticker].iloc[max(0, pos - _TURNOVER_WINDOW + 1): pos + 1]
                turnover = float(trail.median()) if trail.notna().any() else np.nan
                recs.append((score, fwd, base, turnover))
            if len(recs) < 10:
                continue
            df = pd.DataFrame(recs, columns=["score", "fwd", "base", "turnover"])
            df["tq"] = df["turnover"].rank(pct=True)  # liquidity percentile within date
            for bucket, cut in _LIQ_BUCKETS.items():
                sub = df if cut == 0.0 else df[df["tq"] >= cut]
                if len(sub) < 10:
                    continue
                ic_score[bucket][h].append(rank_ic(sub["score"].to_numpy(), sub["fwd"].to_numpy()))
                ic_base[bucket][h].append(rank_ic(sub["base"].to_numpy(), sub["fwd"].to_numpy()))
                decile_rows[bucket][h].append(sub[["score", "fwd"]])

    return {
        "n_dates": len(rebal),
        "avg_coverage": float(np.mean(coverage)) if coverage else 0.0,
        "buckets": {
            bucket: {
                h: {
                    "score": _ic_summary(ic_score[bucket][h]),
                    "baseline_12_1": _ic_summary(ic_base[bucket][h]),
                    "deciles": _decile_summary(decile_rows[bucket][h]),
                }
                for h in _HORIZONS
            }
            for bucket in _LIQ_BUCKETS
        },
    }


def _ic_summary(ics: list[float]) -> dict:
    arr = np.asarray([v for v in ics if np.isfinite(v)], dtype=float)
    if arr.size == 0:
        return {"n": 0, "mean_ic": float("nan")}
    mean, std = float(arr.mean()), float(arr.std(ddof=1)) if arr.size > 1 else 0.0
    ir = mean / std if std > 0 else float("nan")
    return {
        "n": int(arr.size),
        "mean_ic": round(mean, 4),
        "std_ic": round(std, 4),
        "ic_ir": round(ir, 3),               # mean/std
        "t_stat": round(ir * np.sqrt(arr.size), 2) if np.isfinite(ir) else float("nan"),
        "pct_positive": round(float((arr > 0).mean()), 3),
    }


def _decile_summary(frames: list[pd.DataFrame]) -> dict:
    """Top-minus-bottom decile spread, averaged across dates."""
    spreads: list[float] = []
    per_decile: dict[int, list[float]] = defaultdict(list)
    for g in frames:
        g = g.dropna(subset=["score", "fwd"])
        if len(g) < 10:
            continue
        dec = pd.qcut(g["score"].rank(method="first"), 10, labels=False)
        means = g.groupby(dec)["fwd"].mean()
        if 0 in means.index and 9 in means.index:
            spreads.append(float(means[9] - means[0]))
        for d_, v in means.items():
            per_decile[int(d_)].append(float(v))
    if not spreads:
        return {"n": 0}
    sp = np.asarray(spreads, dtype=float)
    t = float(sp.mean() / sp.std(ddof=1) * np.sqrt(sp.size)) if sp.size > 1 and sp.std(ddof=1) > 0 else float("nan")
    return {
        "n": int(sp.size),
        "mean_spread_top_minus_bottom": round(float(sp.mean()), 4),
        "spread_t_stat": round(t, 2),
        "decile_mean_fwd": {d_: round(float(np.mean(v)), 4) for d_, v in sorted(per_decile.items())},
    }


def _print_report(rep: dict) -> None:
    log.info("=" * 64)
    log.info("MOMENTUM SCORE VALIDATION (point-in-time, survivorship-aware)")
    log.info("rebalance dates=%d  avg universe/date=%.0f", rep["n_dates"], rep["avg_coverage"])
    for bucket, horizons in rep["buckets"].items():
        log.info("=" * 64)
        log.info("LIQUIDITY BUCKET: %s", bucket)
        for h, blk in horizons.items():
            s, b, d = blk["score"], blk["baseline_12_1"], blk["deciles"]
            log.info("  horizon=%dd (n=%d)  SCORE mean_IC=%-8s t=%-6s pos%%=%s",
                     h, s.get("n", 0), s.get("mean_ic"), s.get("t_stat"), s.get("pct_positive"))
            log.info("                    BASE  mean_IC=%-8s t=%-6s",
                     b.get("mean_ic"), b.get("t_stat"))
            log.info("                    decile top-bottom spread=%s (t=%s)",
                     d.get("mean_spread_top_minus_bottom"), d.get("spread_t_stat"))


async def main() -> dict:
    pool = await get_batch_pool()
    closes, turns = await load_price_panel(pool, active_only=False)
    log.info("loaded panel for %d tickers (incl. delisted)", len(closes))
    rep = evaluate(closes, turns)
    _print_report(rep)
    return rep


if __name__ == "__main__":
    asyncio.run(main())
