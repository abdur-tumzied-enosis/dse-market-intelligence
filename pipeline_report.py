"""
Pipeline verification report — query pipeline_jobs + source_health + pipeline_alerts
and print a pass/fail summary.

Usage:
    python pipeline_report.py                  # last 1 hour
    python pipeline_report.py --hours 24       # last 24 hours
    python pipeline_report.py --hours 168      # last week

Exit code: 0 = all pass, 1 = failures found.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone

import asyncpg


async def report(hours: int, dsn: str) -> bool:
    """Return True if all checks pass."""
    conn = await asyncpg.connect(dsn)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    ok = True

    print(f"\n{'='*60}")
    print(f"  DSE Pipeline Report — last {hours}h")
    print(f"  Since: {since.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    # ── 1. Job success/failure counts ─────────────────────────────────────
    rows = await conn.fetch(
        """
        SELECT
            job_name,
            COUNT(*) FILTER (WHERE status = 'success') AS success,
            COUNT(*) FILTER (WHERE status = 'failed')  AS failed,
            COUNT(*) FILTER (WHERE status = 'running') AS running,
            MAX(finished_at) AS last_run
        FROM pipeline_jobs
        WHERE started_at >= $1
        GROUP BY job_name
        ORDER BY job_name
        """,
        since,
    )

    print("── Job Runs ─────────────────────────────────────────────────")
    if not rows:
        print("  [WARN] No job runs recorded in this window.\n")
        ok = False
    else:
        for r in rows:
            status = "PASS" if r["failed"] == 0 else "FAIL"
            if r["failed"] > 0:
                ok = False
            last = r["last_run"].strftime("%H:%M UTC") if r["last_run"] else "never"
            print(
                f"  [{status}] {r['job_name']:<30} "
                f"ok={r['success']}  fail={r['failed']}  running={r['running']}  "
                f"last={last}"
            )

    # ── 2. Zero-record runs (silent failures) ─────────────────────────────
    zero_rows = await conn.fetch(
        """
        SELECT job_name, COUNT(*) AS cnt
        FROM pipeline_jobs
        WHERE started_at >= $1
          AND status = 'success'
          AND records_fetched = 0
          AND stream_name IS NOT NULL
        GROUP BY job_name
        ORDER BY cnt DESC
        """,
        since,
    )

    print("\n── Silent Failures (success + 0 records) ────────────────────")
    if not zero_rows:
        print("  [PASS] No zero-record success runs.")
    else:
        for r in zero_rows:
            print(f"  [WARN] {r['job_name']}: {r['cnt']} run(s) fetched 0 records")
        ok = False

    # ── 3. Source health ──────────────────────────────────────────────────
    health_rows = await conn.fetch(
        """
        SELECT
            source_name,
            COUNT(*) FILTER (WHERE reachable)     AS reachable,
            COUNT(*) FILTER (WHERE NOT reachable)  AS unreachable,
            COUNT(*) FILTER (WHERE structure_hash != prev_hash
                             AND structure_hash IS NOT NULL
                             AND prev_hash IS NOT NULL) AS hash_changes,
            AVG(response_ms) AS avg_ms
        FROM source_health
        WHERE checked_at >= $1
        GROUP BY source_name
        ORDER BY source_name
        """,
        since,
    )

    print("\n── Source Health ─────────────────────────────────────────────")
    if not health_rows:
        print("  [WARN] No health checks recorded in this window.\n")
        ok = False
    else:
        for r in health_rows:
            status = "PASS" if r["unreachable"] == 0 and r["hash_changes"] == 0 else "FAIL"
            if r["unreachable"] > 0 or r["hash_changes"] > 0:
                ok = False
            avg_ms = f"{r['avg_ms']:.0f}ms" if r["avg_ms"] else "n/a"
            print(
                f"  [{status}] {r['source_name']:<20} "
                f"ok={r['reachable']}  down={r['unreachable']}  "
                f"hash_changes={r['hash_changes']}  avg={avg_ms}"
            )

    # ── 4. Alert summary ──────────────────────────────────────────────────
    alert_rows = await conn.fetch(
        """
        SELECT severity, COUNT(*) AS cnt
        FROM pipeline_alerts
        WHERE created_at >= $1
        GROUP BY severity
        ORDER BY
            CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'WARNING' THEN 2 ELSE 3 END
        """,
        since,
    )

    print("\n── Alerts Fired ─────────────────────────────────────────────")
    if not alert_rows:
        print("  [PASS] No alerts fired.")
    else:
        for r in alert_rows:
            flag = "FAIL" if r["severity"] == "CRITICAL" else "WARN"
            if r["severity"] == "CRITICAL":
                ok = False
            print(f"  [{flag}] {r['severity']}: {r['cnt']} alert(s)")

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  RESULT: {'ALL PASS' if ok else 'FAILURES DETECTED'}")
    print(f"{'='*60}\n")

    await conn.close()
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="DSE pipeline verification report")
    parser.add_argument("--hours", type=int, default=1, help="Look-back window in hours (default: 1)")
    parser.add_argument(
        "--dsn",
        default=None,
        help="asyncpg DSN (default: reads DATABASE_URL from .env)",
    )
    args = parser.parse_args()

    if args.dsn is None:
        import os
        from dotenv import load_dotenv
        load_dotenv()
        raw = os.getenv("DATABASE_URL", "")
        # asyncpg wants postgresql:// not postgresql+asyncpg://
        dsn = raw.replace("postgresql+asyncpg://", "postgresql://")
        if not dsn:
            print("ERROR: DATABASE_URL not set. Pass --dsn or set DATABASE_URL.")
            sys.exit(2)
    else:
        dsn = args.dsn

    passed = asyncio.run(report(args.hours, dsn))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
