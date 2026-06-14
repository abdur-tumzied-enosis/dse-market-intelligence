// frontend/lib/server-api.ts
import { cookies } from 'next/headers'
import type {
  MarketIndices, MarketRegime, MarketMovers, MarketSummary, HeatmapItem,
  StockDetail, FundamentalsResponse, PagedResponse, StockListItem,
  AnnouncementsResponse, SectorRow, SectorDetail, AnalyzeResponse,
  TrackRecordResponse,
} from './types'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000'

/** Carries the HTTP status so callers can distinguish 401 (auth) from 404 (not found). */
export class ApiError extends Error {
  constructor(public readonly status: number) {
    super(`HTTP ${status}`)
    this.name = 'ApiError'
  }
}

export async function serverGet<T>(path: string): Promise<T> {
  const cookieStore = await cookies()
  const access = cookieStore.get('dse_access_token')?.value
  const refresh = cookieStore.get('dse_refresh_token')?.value

  let res = await fetch(`${API_BASE}${path}`, {
    headers: access ? { Authorization: `Bearer ${access}` } : {},
    next: { revalidate: 0 },
  })

  // Access cookie is 15 min, refresh cookie 7 days. The browser api client
  // silently refreshes on 401; SSR must do the same or every page 404s ~15 min
  // after login (middleware lets the request through on the refresh cookie
  // alone, but only the access token is sent here). We can't persist the new
  // token — cookies are read-only during render (see next/headers docs) — so we
  // retry in-memory and let the client repersist on its first call. The
  // /api/auth/refresh endpoint is stateless (no rotation), so the parallel page
  // fetches can each refresh without racing.
  if (res.status === 401 && refresh) {
    const refreshed = await fetch(`${API_BASE}/api/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refresh }),
      next: { revalidate: 0 },
    })
    if (refreshed.ok) {
      const { access_token } = (await refreshed.json()) as { access_token: string }
      res = await fetch(`${API_BASE}${path}`, {
        headers: { Authorization: `Bearer ${access_token}` },
        next: { revalidate: 0 },
      })
    }
  }

  if (!res.ok) throw new ApiError(res.status)
  return res.json() as Promise<T>
}

export const serverApi = {
  market: {
    indices: () => serverGet<MarketIndices>('/api/market/indices'),
    regime: () => serverGet<MarketRegime>('/api/market/regime'),
    movers: () => serverGet<MarketMovers>('/api/market/movers'),
    heatmap: () => serverGet<HeatmapItem[]>('/api/market/heatmap'),
    summary: () => serverGet<MarketSummary>('/api/market/summary'),
  },
  stocks: {
    list: (sector?: string, limit = 200, offset = 0) => {
      const p = new URLSearchParams({ limit: String(limit), offset: String(offset) })
      if (sector) p.set('sector', sector)
      return serverGet<PagedResponse<StockListItem>>(`/api/stocks?${p}`)
    },
    detail: (ticker: string) => serverGet<StockDetail>(`/api/stocks/${ticker.toUpperCase()}`),
    fundamentals: (ticker: string) =>
      serverGet<FundamentalsResponse>(`/api/stocks/${ticker.toUpperCase()}/fundamentals`),
    announcements: (ticker: string) =>
      serverGet<AnnouncementsResponse>(`/api/stocks/${ticker.toUpperCase()}/announcements`),
    trackRecord: (ticker: string) =>
      serverGet<TrackRecordResponse>(`/api/stocks/${ticker.toUpperCase()}/track-record`),
    analyze: (ticker: string) =>
      serverGet<AnalyzeResponse>(`/api/analyze/${ticker.toUpperCase()}`),
  },
  sectors: {
    list: () => serverGet<SectorRow[]>('/api/sectors'),
    detail: (sector: string) =>
      serverGet<SectorDetail>(`/api/sectors/${encodeURIComponent(sector)}`),
  },
}
