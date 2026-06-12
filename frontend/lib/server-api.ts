// frontend/lib/server-api.ts
import { cookies } from 'next/headers'
import type {
  MarketIndices, MarketRegime, MarketMovers, MarketSummary, HeatmapItem,
  StockDetail, FundamentalsResponse, PagedResponse, StockListItem,
  AnnouncementsResponse, SectorRow, SectorDetail, AnalyzeResponse,
  TrackRecordResponse,
} from './types'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000'

export async function serverGet<T>(path: string): Promise<T> {
  const cookieStore = await cookies()
  const token = cookieStore.get('dse_access_token')?.value
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${API_BASE}${path}`, {
    headers,
    next: { revalidate: 0 },
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
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
