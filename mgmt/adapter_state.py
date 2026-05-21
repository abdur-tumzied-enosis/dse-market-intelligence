"""
In-memory adapter override state with DB persistence.

Overrides (paused flag, priority adjustments) are stored in the adapter_overrides
table and loaded on startup so they survive restarts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import asyncpg

_overrides: dict[tuple[str, str], "_AdapterOverride"] = {}


@dataclass
class _AdapterOverride:
    stream_name: str
    adapter_name: str
    paused: bool = False
    priority_delta: int = 0
    reason: str = ""


def get_override(stream: str, adapter: str) -> _AdapterOverride:
    return _overrides.get((stream, adapter), _AdapterOverride(stream, adapter))


def list_overrides() -> list[dict[str, Any]]:
    return [
        {
            "stream_name": o.stream_name,
            "adapter_name": o.adapter_name,
            "paused": o.paused,
            "priority_delta": o.priority_delta,
            "reason": o.reason,
        }
        for o in _overrides.values()
    ]


async def load_overrides(pool: asyncpg.Pool) -> None:
    rows = await pool.fetch("SELECT * FROM adapter_overrides")
    for r in rows:
        key = (r["stream_name"], r["adapter_name"])
        _overrides[key] = _AdapterOverride(
            stream_name=r["stream_name"],
            adapter_name=r["adapter_name"],
            paused=r["paused"],
            priority_delta=r["priority_delta"],
            reason=r["reason"] or "",
        )


async def set_override(
    pool: asyncpg.Pool,
    stream: str,
    adapter: str,
    **kwargs: Any,
) -> _AdapterOverride:
    key = (stream, adapter)
    existing = _overrides.get(key, _AdapterOverride(stream, adapter))
    for k, v in kwargs.items():
        setattr(existing, k, v)
    _overrides[key] = existing

    await pool.execute(
        """
        INSERT INTO adapter_overrides
            (stream_name, adapter_name, paused, priority_delta, reason, updated_at)
        VALUES ($1, $2, $3, $4, $5, NOW())
        ON CONFLICT (stream_name, adapter_name) DO UPDATE
        SET paused         = EXCLUDED.paused,
            priority_delta = EXCLUDED.priority_delta,
            reason         = EXCLUDED.reason,
            updated_at     = NOW()
        """,
        stream,
        adapter,
        existing.paused,
        existing.priority_delta,
        existing.reason,
    )
    return existing
