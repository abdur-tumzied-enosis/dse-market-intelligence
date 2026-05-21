from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from extraction.registry import STREAMS
from mgmt.adapter_state import get_override, set_override
from mgmt.deps import get_db

router = APIRouter(prefix="/mgmt/streams", tags=["streams"])


def _adapter_info(stream_name: str, adapter: Any) -> dict:
    ov = get_override(stream_name, adapter.name)
    return {
        "name": adapter.name,
        "base_priority": adapter.priority,
        "effective_priority": adapter.priority + ov.priority_delta,
        "priority_delta": ov.priority_delta,
        "paused": ov.paused,
        "timeout_seconds": adapter.timeout_seconds,
    }


def _stream_summary(name: str) -> dict:
    stream = STREAMS[name]
    adapters = [_adapter_info(name, a) for a in stream.adapters]
    active = [a for a in adapters if not a["paused"]]
    return {
        "name": name,
        "adapter_count": len(adapters),
        "active_adapters": len(active),
        "adapters": adapters,
    }


@router.get("")
async def list_streams():
    return [_stream_summary(n) for n in STREAMS]


@router.get("/{name}")
async def get_stream(name: str):
    if name not in STREAMS:
        raise HTTPException(404, f"Stream '{name}' not found")
    return _stream_summary(name)


def _validate_stream_adapter(stream: str, adapter: str) -> None:
    if stream not in STREAMS:
        raise HTTPException(404, f"Stream '{stream}' not found")
    names = {a.name for a in STREAMS[stream].adapters}
    if adapter not in names:
        raise HTTPException(404, f"Adapter '{adapter}' not in stream '{stream}'")


@router.post("/{stream}/adapters/{adapter}/pause")
async def pause_adapter(stream: str, adapter: str, pool=Depends(get_db)):
    _validate_stream_adapter(stream, adapter)
    await set_override(pool, stream, adapter, paused=True, reason="manual pause")
    return {"status": "paused", "stream": stream, "adapter": adapter}


@router.post("/{stream}/adapters/{adapter}/resume")
async def resume_adapter(stream: str, adapter: str, pool=Depends(get_db)):
    _validate_stream_adapter(stream, adapter)
    await set_override(pool, stream, adapter, paused=False, reason="manual resume")
    return {"status": "active", "stream": stream, "adapter": adapter}


@router.post("/{stream}/adapters/{adapter}/promote")
async def promote_adapter(stream: str, adapter: str, pool=Depends(get_db)):
    _validate_stream_adapter(stream, adapter)
    ov = get_override(stream, adapter)
    new_delta = ov.priority_delta - 1
    await set_override(pool, stream, adapter, priority_delta=new_delta, reason="manual promote")
    return {"status": "promoted", "stream": stream, "adapter": adapter, "priority_delta": new_delta}


@router.post("/{stream}/adapters/{adapter}/demote")
async def demote_adapter(stream: str, adapter: str, pool=Depends(get_db)):
    _validate_stream_adapter(stream, adapter)
    ov = get_override(stream, adapter)
    new_delta = ov.priority_delta + 1
    await set_override(pool, stream, adapter, priority_delta=new_delta, reason="manual demote")
    return {"status": "demoted", "stream": stream, "adapter": adapter, "priority_delta": new_delta}
