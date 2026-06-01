# Consumer Frontend + Backend API — Design Spec
**Date:** 2026-05-26  
**Scope:** Layer 5 (FastAPI consumer API) + Layer 6 (Next.js 16 consumer frontend)  
**Status:** Approved, ready for implementation planning

---

## 1. Overview

Consumer-facing web application for the DSE Stock Intelligence Platform. Targets retail and institutional investors on the Dhaka Stock Exchange. Provides market data, AI-powered stock analysis, ML predictions, portfolio management, and an LLM chat analyst.

**Services:**
```
Next.js 16.2.6 (:3000)  →  FastAPI consumer API (:8000)  →  PostgreSQL / Redis / ML models
                                      ↕
                            mgmt API (:8001) — internal only, unchanged
```

---

## 2. Architecture

### Approach
Separate FastAPI backend + Next.js 16 frontend. Next.js App Router with Server Components for data-heavy pages (SSR for speed + SEO). Client components only where interactivity or real-time data is needed. TanStack Query v5 for client-side cache and mutations.

### Directory Layout

```
api/                          ← new FastAPI Layer 5 service
  routers/
    auth.py
    market.py
    stocks.py
    sectors.py
    chat.py
    screen.py
    portfolio.py
    reports.py
    user.py
  deps.py                     ← JWT auth dependency, tier inject
  main.py
  middleware/
    rate_limit.py

frontend/                     ← new Next.js 16.2.6 app
  app/
    (auth)/                   ← login, register, verify-email (no nav)
      login/page.tsx
      register/page.tsx
      verify-email/page.tsx
    (app)/                    ← all authenticated pages (shared layout)
      layout.tsx              ← Sidebar + TopBar
      dashboard/page.tsx
      stocks/page.tsx
      stocks/[ticker]/page.tsx
      predict/[ticker]/page.tsx
      sectors/page.tsx
      chat/page.tsx
      portfolio/page.tsx
      reports/page.tsx
      settings/page.tsx
    page.tsx                  ← landing page (unauthenticated)
    layout.tsx                ← root layout
  components/
    layout/
    stocks/
    chat/
    market/
    ui/
  lib/
    api.ts                    ← typed API client (fetch wrapper)
    auth.ts                   ← token helpers
    hooks/                    ← TanStack Query hooks per resource
  middleware.ts               ← auth redirect
```

---

## 3. Authentication

### Flow
```
Register → email verification → login → JWT issued
```

1. `POST /auth/register` — stores user with `is_verified=false`, sends verification email
2. `POST /auth/verify-email` — activates account
3. `POST /auth/login` — returns `access_token` (15 min JWT) + sets `refresh_token` httpOnly cookie (7 days)
4. `POST /auth/refresh` — reads refresh cookie, issues new access token
5. `POST /auth/logout` — clears refresh cookie

### JWT Payload
```json
{ "sub": "user_id", "tier": "free" | "pro", "exp": 1234567890 }
```

### Next.js Middleware
`middleware.ts` runs on every `/(app)/*` route. Reads access token from cookie. Redirects unauthenticated requests to `/login`. On 401 from API, triggers silent refresh via `/auth/refresh`; if refresh fails, redirects to `/login`.

### DB Migration
Migration `023_users.sql`:
```sql
CREATE TABLE users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    tier        TEXT NOT NULL DEFAULT 'free',  -- 'free' | 'pro'
    is_verified BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 4. Access Control

### Guest (unauthenticated)
Landing page only (`/`). All other routes redirect to `/login`.

### Tier Matrix

| Route | Free | Pro |
|---|---|---|
| `/dashboard` | Market overview only (no watchlist) | Full with watchlist |
| `/stocks` | Screener visible, health score blurred | Full |
| `/stocks/[ticker]` | Price chart + 3yr fundamentals | Full 10yr + valuation + DCF |
| `/predict/[ticker]` | Paywall overlay | Full ML predictions |
| `/sectors` | Full | Full |
| `/chat` | 3 queries/day | 30 queries/day |
| `/portfolio` | Max 5 holdings, no risk analysis | Unlimited + ML risk analysis |
| `/reports` | Paywall overlay | Full PDF generation |
| `/settings` | Full | Full |

### Paywall UX
- Blurred content with "Upgrade to Pro" overlay — not a hard redirect
- Chat quota: `QuotaChip` in sidebar shows `2/3 queries used today`, resets midnight BD time
- Hard 429 block at quota limit with reset time shown

---

## 5. API Layer (Layer 5)

### Middleware Stack
```
request → CORS → JWT decode → tier inject → rate-limit check → handler
```

### Rate Limiting
- Redis key: `ratelimit:{user_id}:{date}` (two sub-keys: `api_calls`, `chat_queries`)
- TTL: seconds until midnight Asia/Dhaka
- 429 response includes `Retry-After` and `X-RateLimit-Reset` headers
- Limits: Free = 50 API / 3 chat; Pro = 1000 API / 30 chat

### Endpoint Reference

```
Auth:
  POST /auth/register
  POST /auth/verify-email
  POST /auth/login
  POST /auth/refresh
  POST /auth/logout

Market (Redis cached, all tiers):
  GET  /api/market/summary      TTL 5min   — DSEX, DS30, DSES + change%
  GET  /api/market/movers       TTL 5min   — top 10 gainers/losers/volume
  GET  /api/market/heatmap      TTL 15min  — sector performance grid

Stocks:
  GET  /api/stocks                         — list: ticker, price, change%, health_score, rating, sector
  GET  /api/stocks/{ticker}                — tier-gated: free=3yr data, pro=10yr
  GET  /api/stocks/{ticker}/price          — ?from=&to=&interval=
  GET  /api/stocks/{ticker}/fundamentals   — ?years=N (free: max 3, pro: max 10)
  GET  /api/stocks/{ticker}/news
  GET  /api/stocks/{ticker}/dividends
  GET  /api/stocks/{ticker}/predictions/{horizon}   — pro only

Sectors:
  GET  /api/sectors
  GET  /api/sectors/{sector}/stocks

Chat (SSE, rate-limited):
  POST /api/chat                           — SSE stream, quota enforced server-side
  GET  /api/chat/quota                     — { used, limit, resets_at }

Screener:
  POST /api/screen                         — filter by sector/PE/health_score/rating/market_cap

Portfolio:
  GET    /api/portfolio
  POST   /api/portfolio/holdings
  PUT    /api/portfolio/holdings/{id}
  DELETE /api/portfolio/holdings/{id}
  GET    /api/portfolio/analysis           — pro only

Reports (pro only):
  POST /api/reports/generate
  GET  /api/reports
  GET  /api/reports/{id}/download

User:
  GET /api/user/me
  PUT /api/user/me
```

---

## 6. Frontend Design System

### Stack
- **Framework:** Next.js 16.2.6 (App Router, TypeScript)
- **Styling:** Tailwind CSS v4
- **UI primitives:** shadcn/ui (dark theme, slate palette)
- **Data fetching:** TanStack Query v5 (client), Next.js Server Components (SSR)
- **Price charts:** TradingView Lightweight Charts v5
- **Data charts:** Recharts v2 (fundamentals, EPS, dividends)
- **Icons:** Lucide React
- **SSE:** native `EventSource` / custom hook

### Design Tokens
```
bg-base:      #0a0a0f   — near-black background
bg-surface:   #111118   — cards, panels
bg-elevated:  #1a1a24   — modals, dropdowns
border:       #2a2a3a
text-primary: #e8e8f0
text-muted:   #6b6b80
accent-green: #00d4a4   — price up, BUY
accent-red:   #ff4d6a   — price down, SELL
accent-blue:  #4d9eff   — links, CTA
accent-gold:  #f5c842   — HOLD, warnings
```

### Component Tree
```
components/
  layout/
    Sidebar.tsx           — nav links, QuotaChip, tier badge
    TopBar.tsx            — live DSEX/DS30 market strip
    AuthLayout.tsx        — centered card for login/register pages

  stocks/
    HealthGauge.tsx       — SVG circular gauge 0–100, color-coded by threshold
    RatingBadge.tsx       — STRONG_BUY / BUY / HOLD / SELL / STRONG_SELL chip
    PriceChart.tsx        — TradingView Lightweight Charts wrapper
    FundamentalsChart.tsx — Recharts: EPS/revenue/PE bar charts
    StockCard.tsx         — compact row for screener and watchlist
    PredictionFanChart.tsx — bear/base/bull price bands overlay on chart

  chat/
    ChatWindow.tsx        — SSE streaming, message list, scroll-to-bottom
    ToolCallIndicator.tsx — animated spinner during agent tool calls
    QuickPrompts.tsx      — suggestion chips: "Analyze GP", "Best pharma stocks"

  market/
    HeatmapGrid.tsx       — sector squares, fill color = 30d performance%
    MoverStrip.tsx        — horizontal scrolling ticker tape
    IndexCard.tsx         — DSEX / DS30 / DSES live value + change%

  ui/
    PaywallOverlay.tsx    — blur + "Upgrade to Pro" CTA card linking to /settings#upgrade
    QuotaChip.tsx         — "2/3 queries used · resets in 4h"
    DataTable.tsx         — sortable, filterable table for screener
    Skeleton.tsx          — loading state placeholders
```

### Page Data Strategy
| Page | Rendering | Notes |
|---|---|---|
| `/dashboard` | Server Component (SSR) + Client SSE strip | Fast initial load |
| `/stocks` | Server Component (SSR) | Initial list SSR, filter interactions client-side |
| `/stocks/[ticker]` | Server Component (SSR) | PriceChart is client-only (TradingView) |
| `/predict/[ticker]` | Server Component + client charts | Pro gate server-side |
| `/sectors` | Server Component (SSR) | |
| `/chat` | Full client | SSE streaming |
| `/portfolio` | Server Component + TanStack Query | CRUD mutations client-side |

---

## 7. Incremental Build Plan

Six phases, each a self-contained PR. No phase merges until all items complete.

### Phase A — Foundation
- [ ] `023_users.sql` migration
- [ ] `api/` FastAPI skeleton: main.py, CORS, JWT middleware, deps.py
- [ ] Auth endpoints: register, verify-email, login, refresh, logout
- [ ] `frontend/` Next.js 16.2.6 init: Tailwind v4, shadcn dark theme, TypeScript
- [ ] Sidebar + TopBar layout shell (authenticated route group)
- [ ] `/login` and `/register` pages — connected to real API
- [ ] `middleware.ts` — unauthenticated redirect

### Phase B — Market Dashboard
- [ ] `GET /api/market/summary`, `/movers`, `/heatmap` endpoints
- [ ] `/dashboard` page: IndexCards + MoverStrip + HeatmapGrid
- [ ] TopBar live DSEX strip (SSE)
- [ ] Landing page `/` — hero, features, pricing table, disclaimer footer

### Phase C — Stock Pages
- [x] `GET /api/stocks`, `/api/stocks/{ticker}` (tier-gated)
- [x] `/stocks` screener: DataTable + sector/PE/rating filters
- [x] `/stocks/[ticker]`: PriceChart + HealthGauge + RatingBadge
- [x] FundamentalsChart (3yr free / 10yr pro)
- [x] PaywallOverlay on pro-only sections
- [x] `/sectors` page: sector PE table + heatmap

### Phase D — AI Chat
- [ ] `POST /api/chat` SSE + `GET /api/chat/quota` endpoints
- [ ] Wire to existing `chat/` LangChain agent (Layer 4)
- [ ] `/chat` page: ChatWindow + ToolCallIndicator + QuickPrompts
- [ ] QuotaChip in Sidebar, hard 429 block at limit

### Phase E — Predictions + Portfolio
- [ ] `GET /api/stocks/{ticker}/predictions/{horizon}` (pro only)
- [ ] `/predict/[ticker]`: PredictionFanChart + Monte Carlo bands
- [ ] `024_portfolio.sql` migration — `portfolio_holdings` table (user_id, ticker, quantity, avg_cost, created_at)
- [ ] Portfolio CRUD endpoints + `/api/portfolio/analysis` (pro)
- [ ] `/portfolio` page: holdings table, P&L, sector pie chart

### Phase F — Reports + Polish
- [ ] `POST /api/reports/generate` + download (pro only, WeasyPrint)
- [ ] `/reports` page
- [ ] `/settings` page: profile + tier + quota info
- [ ] Mobile responsive pass (all pages)
- [ ] Error boundaries, loading skeletons, empty states
- [ ] API integration tests (auth flow + tier gating)

### Phase G — Access Control (build before Phase A; consumer API depends on it)
- [ ] `025_access_control.sql` migration (tier_limits, feature_flags, user_access_overrides + seeds)
- [ ] `mgmt/routers/access.py` — all 7 admin endpoints
- [ ] `api/access.py` — `check_feature()` + `get_limit()` with Redis cache
- [ ] Wire consumer API: replace all hardcoded limits + feature gates with `check_feature` / `get_limit`
- [ ] mgmt-ui `/access` page — Tier Limits tab
- [ ] mgmt-ui `/access` page — Feature Flags tab
- [ ] mgmt-ui `/access` page — User Overrides tab (email search + CRUD)
- [ ] Cache invalidation on every mgmt write

**Estimate:** ~15–18 days solo + 2–3 days for Phase G. Each phase is independently shippable.

---

## 10. Access Control System

### Overview
Tier limits and feature gates are fully configurable at runtime via the mgmt API + mgmt-ui. No hardcoded limits in the consumer API. Per-user overrides allow granting or revoking specific features for individual users, bypassing their tier.

### DB Migrations

`025_access_control.sql`:
```sql
-- Configurable numeric limits per tier
CREATE TABLE tier_limits (
    tier        TEXT    NOT NULL,   -- 'free' | 'pro'
    limit_key   TEXT    NOT NULL,   -- 'api_calls_per_day' | 'chat_queries_per_day' | 'portfolio_holdings_max'
    limit_value INTEGER NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (tier, limit_key)
);

-- Seed defaults
INSERT INTO tier_limits VALUES
  ('free', 'api_calls_per_day',       50),
  ('free', 'chat_queries_per_day',     3),
  ('free', 'portfolio_holdings_max',   5),
  ('pro',  'api_calls_per_day',     1000),
  ('pro',  'chat_queries_per_day',    30),
  ('pro',  'portfolio_holdings_max', -1);   -- -1 = unlimited

-- Feature flags per tier
CREATE TABLE feature_flags (
    flag_key    TEXT    NOT NULL,   -- 'predictions' | 'reports' | 'portfolio_analysis' | 'chat' | 'screener_health_score'
    tier        TEXT    NOT NULL,   -- 'free' | 'pro'
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_by  UUID,               -- mgmt user who last changed it
    PRIMARY KEY (flag_key, tier)
);

-- Seed defaults
INSERT INTO feature_flags VALUES
  ('predictions',          'free',  false, NOW(), NULL),
  ('predictions',          'pro',   true,  NOW(), NULL),
  ('reports',              'free',  false, NOW(), NULL),
  ('reports',              'pro',   true,  NOW(), NULL),
  ('portfolio_analysis',   'free',  false, NOW(), NULL),
  ('portfolio_analysis',   'pro',   true,  NOW(), NULL),
  ('chat',                 'free',  true,  NOW(), NULL),
  ('chat',                 'pro',   true,  NOW(), NULL),
  ('screener_health_score','free',  false, NOW(), NULL),
  ('screener_health_score','pro',   true,  NOW(), NULL);

-- Per-user access overrides
CREATE TABLE user_access_overrides (
    id          UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID    NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    flag_key    TEXT    NOT NULL,
    override    TEXT    NOT NULL CHECK (override IN ('grant', 'revoke')),
    expires_at  TIMESTAMPTZ,            -- NULL = permanent
    note        TEXT,                   -- admin note (reason)
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    created_by  UUID,
    UNIQUE (user_id, flag_key)
);

CREATE INDEX ON user_access_overrides (user_id);
```

### Access Resolution (Consumer API)

Every feature-gated endpoint calls `check_feature(user, flag_key)`:

```python
async def check_feature(user: User, flag_key: str) -> bool:
    # 1. Per-user override (highest priority)
    override = await cache_or_db_get_override(user.id, flag_key)
    if override and (override.expires_at is None or override.expires_at > now()):
        return override.override == "grant"

    # 2. Tier feature flag
    flag = await cache_or_db_get_flag(flag_key, user.tier)
    return flag.enabled if flag else False
```

Tier limits resolved similarly via `get_limit(tier, limit_key)` — returns configured value, never hardcoded.

### Caching Strategy
- Redis key: `access:flags:{flag_key}:{tier}` → TTL 60s
- Redis key: `access:limits:{tier}:{limit_key}` → TTL 60s
- Redis key: `access:overrides:{user_id}` → hash of all overrides, TTL 60s
- On any mgmt write → invalidate affected Redis keys immediately

### mgmt API Endpoints

Added to `mgmt/routers/access.py`:

```
Tier Limits:
  GET  /mgmt/access/tier-limits                          → all limits (all tiers)
  PUT  /mgmt/access/tier-limits/{tier}/{key}             → update one limit value

Feature Flags:
  GET  /mgmt/access/features                             → all flags
  PUT  /mgmt/access/features/{flag_key}/{tier}           → { enabled: bool }

User Overrides:
  GET    /mgmt/access/users                              → search users by email
  GET    /mgmt/access/users/{user_id}/overrides          → list overrides for user
  POST   /mgmt/access/users/{user_id}/overrides          → { flag_key, override, expires_at?, note? }
  DELETE /mgmt/access/users/{user_id}/overrides/{flag}   → remove override
```

### mgmt-ui — Access Control Page

New page at `/access` in mgmt-ui. Three tabs:

**Tab 1 — Tier Limits**
Table with columns: `Limit`, `Free`, `Pro`. Inline number edit per cell. Save triggers `PUT` immediately. -1 displayed as "Unlimited".

**Tab 2 — Feature Flags**
Table rows = features, columns = Free / Pro. Each cell is a toggle switch. Visual diff: if free has something pro doesn't, highlight the row.

**Tab 3 — User Overrides**
- Email search box → shows matched users with tier badge
- Click user → shows override list (flag, grant/revoke, expiry, note, delete button)
- "Add Override" form: flag dropdown, grant/revoke select, optional expiry date, note field
- Overrides with `expires_at` in the past shown as greyed-out "Expired" badge

---

## 8. Non-Goals (MVP)

- Payment processing — tier set manually in DB
- Bengali language toggle — English only for now
- Institution tier — Free + Pro only
- WhatsApp/email price alerts — deferred to post-MVP
- PDF annual report upload UI — admin-only for now
- PWA / mobile app

---

## 9. Legal

Disclaimer banner on every authenticated page:
> "This platform provides data and analysis for informational purposes only. It does not constitute investment advice. Past performance does not guarantee future results. Invest at your own risk."
