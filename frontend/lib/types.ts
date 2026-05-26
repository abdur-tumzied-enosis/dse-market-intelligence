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

export interface HeatmapItem {
  ticker: string
  name: string
  sector: string
  change_pct: number | null
  ltp: number | null
  value_bdt: number | null
}
