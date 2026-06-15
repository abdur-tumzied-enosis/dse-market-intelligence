"""
Populate prediction_outcomes once horizons pass and generate accuracy report.

populate_outcomes()         — call nightly (after job_nightly_ml)
check_accuracy_thresholds() — call before quarterly retrain
generate_report()           — call on demand / weekly

Run: python -m ml.monitoring.accuracy_report
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

log = logging.getLogger(__name__)


async def populate_outcomes(pool) -> int:
    """
    Find ml_predictions whose horizon has passed but outcome not yet recorded,
    look up actual closing price, write to prediction_outcomes.

    Returns count of new outcomes written.
    """
    now = datetime.now(UTC)

    pending = await pool.fetch(
        """
        SELECT p.id, p.ticker, p.horizon_days, p.predicted_at,
               p.predicted_direction, p.confidence,
               (
                   SELECT sp.close FROM stock_prices sp
                   WHERE sp.ticker = p.ticker
                     AND sp.time >= p.predicted_at - INTERVAL '1 day'
                     AND sp.time <= p.predicted_at + INTERVAL '2 days'
                   ORDER BY sp.time ASC LIMIT 1
               ) AS price_at_prediction,
               (
                   SELECT sp2.close FROM stock_prices sp2
                   WHERE sp2.ticker = p.ticker
                     AND sp2.time >= p.predicted_at + (p.horizon_days || ' days')::interval
                     AND sp2.time <= p.predicted_at + ((p.horizon_days + 3) || ' days')::interval
                   ORDER BY sp2.time ASC LIMIT 1
               ) AS price_at_horizon
        FROM ml_predictions p
        WHERE NOT EXISTS (
            SELECT 1 FROM prediction_outcomes po WHERE po.prediction_id = p.id
        )
          AND p.predicted_at + (p.horizon_days || ' days')::interval < $1
        """,
        now,
    )

    count = 0
    for row in pending:
        p_price = row["price_at_prediction"]
        h_price = row["price_at_horizon"]
        if p_price is None or h_price is None:
            continue

        p_price = float(p_price)
        h_price = float(h_price)
        return_pct = (h_price - p_price) / p_price * 100
        actual_direction = "up" if h_price > p_price else "down"
        # As of model_version lstm_v2_cal, predicted_direction is an ABSOLUTE
        # up/down call (from the calibrated P(up)), so comparing it to the
        # absolute realized direction here is apples-to-apples. (Pre-lstm_v2_cal
        # rows encoded a RELATIVE 'beats peers' call and this comparison was
        # mismatched — see spec 2026-06-14-lstm-direction-calibration.)
        correct = actual_direction == row["predicted_direction"]

        await pool.execute(
            """
            INSERT INTO prediction_outcomes
                (prediction_id, ticker, horizon_days, predicted_at,
                 predicted_direction, confidence,
                 price_at_prediction, price_at_horizon, return_pct,
                 actual_direction, correct, evaluated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT DO NOTHING
            """,
            row["id"], row["ticker"], row["horizon_days"], row["predicted_at"],
            row["predicted_direction"], float(row["confidence"]),
            p_price, h_price, return_pct, actual_direction, correct,
            datetime.now(UTC),
        )
        count += 1

    log.info("populate_outcomes: evaluated %d predictions", count)
    return count


async def generate_report(pool) -> dict:
    """
    Aggregate accuracy by horizon from prediction_outcomes.

    Returns dict:
        by_horizon: list[{horizon_days, total, correct, accuracy}]
        generated_at: ISO timestamp string
    """
    rows = await pool.fetch(
        """
        SELECT horizon_days,
               COUNT(*)                                          AS total,
               SUM(CASE WHEN correct THEN 1 ELSE 0 END)         AS correct,
               AVG(CASE WHEN correct THEN 1.0 ELSE 0.0 END)     AS accuracy
        FROM prediction_outcomes
        WHERE correct IS NOT NULL
        GROUP BY horizon_days
        ORDER BY horizon_days
        """
    )

    return {
        "by_horizon": [
            {
                "horizon_days": int(r["horizon_days"]),
                "total":        int(r["total"]),
                "correct":      int(r["correct"]),
                "accuracy":     float(r["accuracy"]),
            }
            for r in rows
        ],
        "generated_at": datetime.now(UTC).isoformat(),
    }


async def check_accuracy_thresholds(
    pool,
    warning_threshold: float = 0.48,
    critical_threshold: float = 0.45,
    min_samples: int = 30,
) -> dict:
    """
    Check if any horizon's directional accuracy has dropped below thresholds.

    Horizons with fewer than min_samples evaluated outcomes are skipped —
    not enough data to distinguish model failure from statistical noise.

    NOTE: warning=0.48 / critical=0.45 assume a ~50% base rate. The D1 validation
    (ml.eval.lstm_validation) reports the real per-horizon DSE up-fraction; once
    known, re-set these per horizon relative to that base rate rather than the
    flat defaults.

    Returns dict:
        alert_level:    "CRITICAL" | "WARNING" | None
        worst_horizon:  int | None   (horizon with lowest accuracy)
        worst_accuracy: float | None
        report:         list[dict]   (full by-horizon breakdown)
    """
    report = await generate_report(pool)
    by_horizon = report["by_horizon"]

    worst_accuracy = 1.0
    worst_horizon: int | None = None

    for h in by_horizon:
        if h["total"] < min_samples:
            continue
        if h["accuracy"] < worst_accuracy:
            worst_accuracy = h["accuracy"]
            worst_horizon = h["horizon_days"]

    if worst_horizon is None:
        return {
            "alert_level": None,
            "worst_horizon": None,
            "worst_accuracy": None,
            "report": by_horizon,
        }

    if worst_accuracy < critical_threshold:
        alert_level: str | None = "CRITICAL"
    elif worst_accuracy < warning_threshold:
        alert_level = "WARNING"
    else:
        alert_level = None

    return {
        "alert_level": alert_level,
        "worst_horizon": worst_horizon,
        "worst_accuracy": worst_accuracy,
        "report": by_horizon,
    }


async def main() -> None:
    from db.pool import get_pool
    pool = await get_pool()

    count = await populate_outcomes(pool)
    print(f"Evaluated {count} new prediction outcomes")

    report = await generate_report(pool)
    print("\nAccuracy by horizon:")
    for h in report["by_horizon"]:
        print(f"  {h['horizon_days']:2d}d: {h['accuracy']:.1%} ({h['correct']}/{h['total']})")

    check = await check_accuracy_thresholds(pool)
    if check["alert_level"]:
        print(f"\nALERT [{check['alert_level']}]: {check['worst_horizon']}d horizon = {check['worst_accuracy']:.1%}")
    else:
        print("\nAccuracy OK — no threshold violations")


if __name__ == "__main__":
    asyncio.run(main())
