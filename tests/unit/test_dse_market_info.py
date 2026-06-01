# tests/unit/test_dse_market_info.py
"""Unit tests for DSEDirectMarketInfoAdapter HTML parsing (offline, inline fixture)."""
from decimal import Decimal

import pytest

from extraction.adapters.dse_direct.market_info import (
    DSEDirectMarketInfoAdapter,
    _parse_market_info,
)

# Mirrors the real dse.org/index.php market box (captured 2026-06-01),
# with DSES forced to a down arrow to exercise sign handling.
SAMPLE_HTML = """
<html><body>
 <div class="containbox">
  <section>
   <div class="row white">
    <div class="col-md-6 col-xs-12 col-sm-12 LeftColHome">
     <div class="_row">
      <h2 class="Bodyheading">Last update on Jun 01, 2026 at 10:30 AM</h2>
      <div class="midrow">
       <div class="m_col-1">DSE<font size="+1">X</font>Index</div>
       <div class="m_col-2">5367.86627</div>
       <div class="m_col-3">31.99663</div>
       <div class="m_col-4">0.59965%</div>
       <div class="m_col-5"><img src="assets/images/upArrow.jpg"/></div>
      </div>
      <div class="midrow">
       <div class="m_col-1">DSE<font size="+1">S</font>Index</div>
       <div class="m_col-2">1088.33224</div>
       <div class="m_col-3">5.94121</div>
       <div class="m_col-4">0.5489%</div>
       <div class="m_col-5"><img src="assets/images/downArrow.jpg"/></div>
      </div>
      <div class="midrow">
       <div class="m_col-1">DS30 Index</div>
       <div class="m_col-2">2044.63979</div>
       <div class="m_col-3">13.76018</div>
       <div class="m_col-4">0.67755%</div>
       <div class="m_col-5"><img src="assets/images/upArrow.jpg"/></div>
      </div>
      <div class="midrow mol_col-wid-cus">
       <div class="m_col-wid">Issues Advanced</div>
       <div class="m_col-wid1">Issues declined</div>
       <div class="m_col-wid2">Issues Unchanged</div>
      </div>
      <div class="midrow mol_col-wid-cus">
       <div class="m_col-wid">222</div>
       <div class="m_col-wid1">69</div>
       <div class="m_col-wid2">83</div>
      </div>
     </div>
    </div>
    <div class="col-md-6">other column</div>
   </div>
  </section>
 </div>
</body></html>
"""


@pytest.fixture()
def df():
    raw = _parse_market_info(SAMPLE_HTML)
    return DSEDirectMarketInfoAdapter().normalize(raw)


def test_parses_exactly_three_indices(df):
    assert len(df) == 3
    assert set(df["index_name"]) == {"DSEX", "DSES", "DS30"}


def test_values_are_decimal(df):
    dsex = df[df["index_name"] == "DSEX"].iloc[0]
    assert isinstance(dsex["value"], Decimal)
    assert dsex["value"] == Decimal("5367.86627")


def test_change_pct_signed_by_arrow(df):
    dsex = df[df["index_name"] == "DSEX"].iloc[0]
    dses = df[df["index_name"] == "DSES"].iloc[0]
    assert dsex["change_pct"] == Decimal("0.59965")    # up arrow → positive
    assert dses["change_pct"] == Decimal("-0.5489")    # down arrow → negative


def test_breadth_rows_ignored(df):
    # "Issues Advanced/..." rows must not leak in as indices
    assert df["index_name"].isin({"DSEX", "DSES", "DS30"}).all()


def test_source_tagged(df):
    assert (df["source"] == "dse_direct_market_info").all()


def test_missing_container_raises():
    with pytest.raises(ValueError, match="container not found"):
        _parse_market_info("<html><body><p>nothing</p></body></html>")
