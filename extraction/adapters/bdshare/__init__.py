"""
Monkey-patches for bdshare pandas 3.x + lxml 6.x compatibility.

Patch 1 — read_html bytes fix
  bdshare calls pd.read_html(r.content) where r.content is bytes.
  pandas>=3.0 + lxml>=6.0: lxml.parse() treats bytes as a file path → OSError.
  Fix: wrap bytes in io.BytesIO inside bdshare.stock.market's pd proxy.

Patch 2 — get_company_info slice fix
  bdshare 1.2.1 hardcodes tables[400:] but DSE page now renders ~397 tables.
  tables[400:] is always empty. Fix: return the last 15 tables instead.

Patch 3 — dead domain fix
  bdshare's DSE_ALT_URL = "https://dsebd.com.bd/" no longer resolves, and since
  DSE's 2026 relaunch (www.dse.com.bd) every dsebd.org/*.php page returns
  410 Gone. The legacy PHP site lives on at old.dsebd.org.
  Fix: point both DSE_URL and DSE_ALT_URL at "https://old.dsebd.org/".
"""
from __future__ import annotations

import io
import re


def _apply_bdshare_patches() -> None:
    try:
        import bdshare
        import bdshare.stock.market as _market
        import pandas as _pd
    except ImportError:
        return

    # ------------------------------------------------------------------ #
    # Patch 3: dead domains (dsebd.com.bd, dsebd.org) → old.dsebd.org    #
    # ------------------------------------------------------------------ #
    from bdshare.util import vars as _vs_patch

    _vs_patch.DSE_URL = _vs_patch.DSE_ALT_URL = "https://old.dsebd.org/"

    # ------------------------------------------------------------------ #
    # Patch 1: wrap pd.read_html(bytes) → pd.read_html(BytesIO(bytes))   #
    # ------------------------------------------------------------------ #
    if not getattr(_market.pd, "_bdshare_patched", False):
        _orig_read_html = _pd.read_html

        class _PdProxy:
            _bdshare_patched = True

            def __getattr__(self, name: str):  # type: ignore[override]
                return getattr(_pd, name)

            def read_html(self, io_or_buffer, *args, **kwargs):  # type: ignore[override]
                if isinstance(io_or_buffer, bytes):
                    io_or_buffer = io.BytesIO(io_or_buffer)
                return _orig_read_html(io_or_buffer, *args, **kwargs)

        _market.pd = _PdProxy()

    # ------------------------------------------------------------------ #
    # Patch 2: fix tables[400:] → last 15 tables                         #
    # ------------------------------------------------------------------ #
    if getattr(_market.get_company_info, "_bdshare_slice_patched", False):
        return

    _orig_get_company_info = _market.get_company_info

    from bdshare.util import vars as _vs
    from bdshare.util.helper import BDShareError, safe_get

    def _patched_get_company_info(
        symbol: str, retry_count: int = 3, pause: float = 0.2
    ) -> list:
        r = safe_get(
            _vs.DSE_URL + _vs.DSE_COMPANY_INFO_URL,
            params={"name": symbol},
            alt_url=_vs.DSE_ALT_URL + _vs.DSE_COMPANY_INFO_URL,
            retries=retry_count,
            pause=pause,
        )
        try:
            tables = _pd.read_html(io.BytesIO(r.content))
            # bdshare 1.2.1 used tables[400:] but DSE now renders ~397 tables.
            return tables[-15:] if len(tables) >= 15 else tables
        except Exception as exc:
            raise BDShareError(
                f"Failed to parse company info for {symbol}: {exc}"
            ) from exc

    _patched_get_company_info._bdshare_slice_patched = True  # type: ignore[attr-defined]

    _market.get_company_info = _patched_get_company_info
    bdshare.get_company_info = _patched_get_company_info


_apply_bdshare_patches()
