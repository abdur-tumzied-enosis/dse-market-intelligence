"""DSE market-status smoke test — hits dsebd.org live.

Run: make smoke-test
     python -m pytest tests/smoke/test_dse_market_status_smoke.py -v -s --no-cov

Network required; not part of `make test` (which runs tests/unit/ only). Acts as
a canary for the homepage markup the status parser depends on.
"""
from __future__ import annotations

from extraction.adapters.dse_direct.market_status import DSEMarketStatusAdapter


async def test_dse_market_status_live():
    result = await DSEMarketStatusAdapter().fetch()
    rec = result.data.to_dict("records")[0]
    assert rec["status"] in ("Open", "Closed")
    assert "Market Status" in rec["raw_label"]
