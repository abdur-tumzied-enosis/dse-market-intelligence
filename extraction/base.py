from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


class AdapterError(Exception):
    """Raised when an adapter fails to fetch data."""

    def __init__(self, adapter_name: str, reason: str, retryable: bool = True) -> None:
        self.adapter_name = adapter_name
        self.reason = reason
        self.retryable = retryable
        super().__init__(f"[{adapter_name}] {reason}")


class AllAdaptersFailedError(Exception):
    """Raised by DataStream when every adapter in the chain has failed."""

    def __init__(self, stream_name: str, errors: list[AdapterError]) -> None:
        self.stream_name = stream_name
        self.errors = errors
        reasons = "; ".join(f"{e.adapter_name}: {e.reason}" for e in errors)
        super().__init__(f"All adapters failed for stream '{stream_name}': {reasons}")


@dataclass
class AdapterResult:
    data: pd.DataFrame
    source_name: str
    fetched_at: datetime
    quality: str  # "ok" | "suspect" | "partial"
    records: int
    raw_sample: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.quality not in ("ok", "suspect", "partial"):
            raise ValueError(f"Invalid quality value: {self.quality!r}")
        if self.records != len(self.data):
            self.records = len(self.data)


class BaseAdapter(ABC):
    name: str
    priority: int
    timeout_seconds: int = 15

    @abstractmethod
    async def fetch(self, **kwargs: Any) -> AdapterResult:
        """Fetch data from source. Raise AdapterError on failure."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if source is reachable and returning valid data."""
        ...

    @abstractmethod
    def normalize(self, raw: Any) -> pd.DataFrame:
        """Convert raw source data to canonical schema DataFrame."""
        ...

    async def _fetch_with_timeout(self, **kwargs: Any) -> AdapterResult:
        try:
            return await asyncio.wait_for(self.fetch(**kwargs), timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise AdapterError(
                self.name,
                f"timeout after {self.timeout_seconds}s",
                retryable=True,
            ) from exc


class DataStream:
    """Priority-ordered chain of adapters with automatic failover."""

    def __init__(self, name: str, adapters: list[BaseAdapter]) -> None:
        self.name = name
        # sort ascending so lowest priority number = first tried
        self.adapters = sorted(adapters, key=lambda a: a.priority)

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        errors: list[AdapterError] = []
        for adapter in self.adapters:
            try:
                result = await adapter._fetch_with_timeout(**kwargs)
                if errors:
                    logger.warning(
                        "adapter_failover",
                        stream=self.name,
                        failed=[e.adapter_name for e in errors],
                        succeeded=adapter.name,
                    )
                return result
            except AdapterError as exc:
                logger.warning(
                    "adapter_failed",
                    stream=self.name,
                    adapter=exc.adapter_name,
                    reason=exc.reason,
                )
                errors.append(exc)

        raise AllAdaptersFailedError(self.name, errors)
