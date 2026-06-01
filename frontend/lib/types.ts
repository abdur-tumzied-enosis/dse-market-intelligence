// frontend/lib/types.ts
export interface MarketIndices {
  dsex_value: number
  dsex_change_pct: number
  ds30_value: number
  ds30_change_pct: number
  dses_value: number
  dses_change_pct: number
  market_status: string
  advance: number
  decline: number
  unchanged: number
}

export interface TopMover {
  ticker: string
  name: string
  close: number
  change_pct: number
}

export interface MarketMovers {
  gainers: TopMover[]
  losers: TopMover[]
}

export interface MarketSummary {
  total_stocks: number
  advance: number
  decline: number
  unchanged: number
  total_volume: number | null
  total_value_bdt: number | null
  avg_change_pct: number | null
}

export interface MarketRegime {
  regime: 'Bull' | 'Bear' | 'Unknown'
  dsex: number | null
  ma: number | null
  window: number
  provisional: boolean
  distance_pct: number | null
  as_of: string | null
  data_status: 'ok' | 'provisional' | 'insufficient'
}

export interface HeatmapItem {
  ticker: string
  name: string
  sector: string
  change_pct: number | null
  ltp: number | null
  value_bdt: number | null
  market_cap: number | null
}

// ─── Stocks ──────────────────────────────────────────────────────────────────

export interface StockListItem {
  ticker: string
  name: string
  sector: string
  category: string | null
  market_cap_bdt: number | null
  is_active: boolean
}

export interface PagedResponse<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface CompanyInfo {
  ticker: string
  name: string
  sector: string
  category: string | null
  market_cap_bdt: number | null
  is_active: boolean
  listing_date: string | null
  isin: string | null
}

export interface LatestPrice {
  close: number
  change_pct: number | null
  volume: number | null
  value_bdt: number | null
  high: number | null
  low: number | null
  time: string
}

export interface LivePrice {
  ticker: string
  available: boolean
  ltp: number | null
  high: number | null
  low: number | null
  prev_close: number | null
  change_pct: number | null
  volume: number | null
  value_bdt: number | null
  market_status: string
  as_of: string
}

export interface LatestFundamentals {
  eps: number | null
  nav: number | null
  pe: number | null
  cash_div_pct: number | null
  stock_div_pct: number | null
  sponsor_pct: number | null
  public_pct: number | null
  fiscal_year: number | null
}

export interface HealthScore {
  health_score: number | null
  fundamental_score: number | null
  momentum_score: number | null
  scored_at: string
}

export interface StockDetail {
  company: CompanyInfo
  latest_price: LatestPrice | null
  fundamentals: LatestFundamentals | null
  health_score: HealthScore | null
}

export interface OHLCVPoint {
  day: string
  open: number | null
  high: number | null
  low: number | null
  close: number
  volume: number | null
  value_bdt: number | null
}

export interface OHLCVResponse {
  ticker: string
  interval: string
  items: OHLCVPoint[]
}

export interface FundamentalsRow {
  fiscal_year: number | null
  eps: number | null
  nav: number | null
  pe: number | null
  cash_div_pct: number | null
  stock_div_pct: number | null
  sponsor_pct: number | null
  public_pct: number | null
  fetched_at: string
}

export interface FundamentalsResponse {
  ticker: string
  items: FundamentalsRow[]
}

export interface AnnouncementRow {
  id: number
  published_at: string
  headline: string
  announcement_type: string | null
  eps_value: number | null
  eps_period: string | null
  dividend_cash_pct: number | null
  dividend_stock_pct: number | null
}

export interface AnnouncementsResponse {
  ticker: string
  total: number
  items: AnnouncementRow[]
}

// ─── Wyckoff overlay ─────────────────────────────────────────────────────────
// Field names + literals mirror the backend schema in
// docs/superpowers/specs/2026-06-01-wyckoff-overlay-design.md ("Response schema").

export type WyckoffEventType =
  | 'SC'
  | 'BC'
  | 'SPRING'
  | 'PS'
  | 'AR'
  | 'ST'
  | 'TEST'
  | 'SOS'
  | 'LPS'
  | 'PSY'
  | 'UT'
  | 'UTAD'
  | 'SOW'
  | 'LPSY'
export type WyckoffPhase = 'accumulation' | 'distribution' | 'undetermined'

export interface WyckoffEvent {
  day: string                 // backend `date` serialized as ISO YYYY-MM-DD
  type: WyckoffEventType
  price: number               // backend `Decimal` serialized as number/string
  label: string               // short marker label, e.g. "SC"
  help: string                // plain-language definition
}

export interface WyckoffRange {
  start_day: string
  end_day: string
  phase: WyckoffPhase
  phase_help: string
  confidence: number          // 0..1
  support: number
  resistance: number
  events: WyckoffEvent[]
}

export interface WyckoffResponse {
  ticker: string
  interval: string
  ranges: WyckoffRange[]
}
