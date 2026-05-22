from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from mgmt.deps import get_db

router = APIRouter(tags=["metrics"])

_METRICS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics(pool=Depends(get_db)) -> str:
    """Prometheus scrape endpoint — exposes pipeline, quality, and health gauges."""
    lines: list[str] = []

    def g(name: str, help_: str, typ: str = "gauge") -> None:
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} {typ}")

    # ── pipeline_jobs ───────────────────────────────────────────────────────
    job_rows = await pool.fetch(
        """
        SELECT status, COUNT(*) AS cnt
        FROM pipeline_jobs
        GROUP BY status
        """
    )
    g("dse_pipeline_jobs_total", "Total pipeline job runs by status", "gauge")
    for row in job_rows:
        lines.append(f'dse_pipeline_jobs_total{{status="{row["status"]}"}} {row["cnt"]}')

    # last job duration per stream
    dur_rows = await pool.fetch(
        """
        SELECT DISTINCT ON (stream_name)
            stream_name, duration_ms, status
        FROM pipeline_jobs
        ORDER BY stream_name, started_at DESC
        """
    )
    g("dse_pipeline_last_duration_ms", "Duration of the most recent job run per stream")
    for row in dur_rows:
        if row["duration_ms"] is not None:
            lines.append(
                f'dse_pipeline_last_duration_ms{{stream="{row["stream_name"]}",'
                f'status="{row["status"]}"}} {row["duration_ms"]}'
            )

    # records inserted per stream (last successful run)
    rec_rows = await pool.fetch(
        """
        SELECT DISTINCT ON (stream_name)
            stream_name, records_inserted
        FROM pipeline_jobs
        WHERE status = 'success'
        ORDER BY stream_name, started_at DESC
        """
    )
    g("dse_pipeline_last_records_inserted", "Records inserted in the most recent successful run")
    for row in rec_rows:
        if row["records_inserted"] is not None:
            lines.append(
                f'dse_pipeline_last_records_inserted{{stream="{row["stream_name"]}"}} '
                f'{row["records_inserted"]}'
            )

    # ── quality failures ────────────────────────────────────────────────────
    qf_rows = await pool.fetch(
        """
        SELECT stream_name, SUM(quality_failures) AS total_failures
        FROM pipeline_jobs
        WHERE quality_failures > 0
        GROUP BY stream_name
        """
    )
    g("dse_quality_failures_total", "Cumulative quality check failures by stream", "counter")
    for row in qf_rows:
        lines.append(
            f'dse_quality_failures_total{{stream="{row["stream_name"]}"}} {row["total_failures"]}'
        )

    # ── source health ────────────────────────────────────────────────────────
    health_rows = await pool.fetch(
        """
        SELECT DISTINCT ON (source_name)
            source_name, reachable, response_ms, hash_changed
        FROM source_health
        ORDER BY source_name, checked_at DESC
        """
    )
    g("dse_source_reachable", "1 if source was reachable on last check, 0 otherwise")
    for row in health_rows:
        val = 1 if row["reachable"] else 0
        lines.append(f'dse_source_reachable{{source="{row["source_name"]}"}} {val}')

    g("dse_source_response_ms", "Last HTTP response time per source in milliseconds")
    for row in health_rows:
        if row["response_ms"] is not None:
            lines.append(
                f'dse_source_response_ms{{source="{row["source_name"]}"}} {row["response_ms"]}'
            )

    g("dse_source_structure_changed", "1 if source HTML structure changed on last check")
    for row in health_rows:
        val = 1 if row["hash_changed"] else 0
        lines.append(
            f'dse_source_structure_changed{{source="{row["source_name"]}"}} {val}'
        )

    # ── adapter overrides (paused) ──────────────────────────────────────────
    override_rows = await pool.fetch(
        """
        SELECT COUNT(*) AS cnt FROM adapter_overrides WHERE paused = true
        """
    )
    g("dse_adapters_paused", "Number of adapters currently paused via override")
    lines.append(f'dse_adapters_paused {override_rows[0]["cnt"]}')

    lines.append("")  # trailing newline
    return "\n".join(lines)
