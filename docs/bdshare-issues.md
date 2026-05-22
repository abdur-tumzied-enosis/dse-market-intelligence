# bdshare 1.2.1 — Known Issues

## 1. `get_company_info()` — HTML parsing bug ✅ PATCHED

**Affected adapters:** `BDShareCompanyInfoAdapter`

**Error:**
```
OSError: Error reading file '<!DOCTYPE html>
<html lang="en">
<head>
    <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
    <title>Display Company Information | Dhaka Stock Exchange</title>
...
```

**Root cause:**
bdshare passes `r.content` (bytes) to `pd.read_html()`, which under pandas 3.x + lxml 6.x routes the content to lxml's `parse()` function. lxml's `parse()` treats a byte string as a **file path** rather than raw HTML content, so it tries to `open("<!DOCTYPE html>...")` as a file and throws `OSError`.

**Fix applied:** `extraction/adapters/bdshare/__init__.py` monkey-patches `bdshare.stock.market.pd` with a thin proxy that wraps `bytes` arguments in `io.BytesIO` before delegating to `pd.read_html`. Applied once at package import. `tests/conftest.py` applies the same patch before any pytest run (including smoke tests).

**Affected versions:** bdshare 1.2.1 + pandas >=3.0 + lxml >=6.0

---

## 2. `get_sector_performance()`, `get_top_gainers_losers()` — empty outside market hours

**Affected adapters:** `BDShareSectorAdapter`

**Error:**
```
bdshare.util.helper.BDShareError: No sector performance data found.
bdshare.util.helper.BDShareError: No top gainers/losers data found.
```

**Root cause:** DSE pages for these endpoints serve no data rows outside the trading session. bdshare parses the empty table and raises `BDShareError`. Not a bug — expected behaviour.

**To verify:** Re-run smoke tests during DSE market hours: **Sun–Thu 10:00–14:30 BD (04:00–08:30 UTC)**

```bash
python -m pytest tests/smoke/test_bdshare_smoke.py::test_get_sector_performance \
                 tests/smoke/test_bdshare_smoke.py::test_get_top_gainers_losers \
                 -v -s --no-cov
```

---

## 3. `get_corporate_announcements()`, `get_price_sensitive_news()` — empty outside market hours

**Affected adapters:** `BDShareAnnouncementsAdapter`, `BDSharePSNAdapter`

Same situation as #2 — no announcements/PSN posted post-close. Will return data during market session when companies are publishing.

```bash
python -m pytest tests/smoke/test_bdshare_smoke.py::test_get_corporate_announcements \
                 tests/smoke/test_bdshare_smoke.py::test_get_price_sensitive_news \
                 -v -s --no-cov
```

---

## 4. `get_market_depth_data()` — empty outside market hours

**Affected adapters:** `BDShareDepthAdapter`

Order book is empty when market is closed. Returns empty DataFrame `(0, 0)`. Structure (5-level buy/sell) still unconfirmed — need market-hours run.

```bash
python -m pytest tests/smoke/test_bdshare_smoke.py::test_get_market_depth_data -v -s --no-cov
```

---

## 5. Deprecated API names

Two methods used in smoke tests and adapters are deprecated:

| Old (deprecated) | New |
|---|---|
| `get_hist_data(start, end, code)` | `get_historical_data(start, end, code)` |
| `get_basic_hist_data(start, end, code)` | `get_basic_historical_data(start, end, code)` |

Both adapters and smoke tests already updated to use new names (2026-05-21).

---

## 6. `get_latest_pe()` — no column headers

Returns DataFrame with numeric columns `[0, 1, 2, 3, 4, 5, 6, 7, 8]` — no named headers. Mapped empirically:

| Col | Value |
|---|---|
| 0 | ticker symbol |
| 1 | LTP |
| 2 | close |
| 3 | P/E ratio (or `n/a`) |
| 4–6 | unknown / empty |
| 7 | EPS (or `n/a`) |
| 8 | unknown |

Columns 4–6 are consistently empty in observed data. Source is `cols[1:10]` from the HTML table — exact header mapping needs DSE page inspection.
