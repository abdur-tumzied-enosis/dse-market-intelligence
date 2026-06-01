// frontend/lib/screener.ts
import type { StockListItem } from './types'

export interface ScreenerFilters {
  sector: string          // 'All' or a sector name
  search: string
  maxPe: number | null    // null = no PE ceiling
  rating: string          // 'All' or a rating value
}

export function matchesFilters(s: StockListItem, f: ScreenerFilters): boolean {
  if (f.sector !== 'All' && s.sector !== f.sector) return false
  if (f.rating !== 'All' && s.rating !== f.rating) return false
  if (f.maxPe !== null) {
    if (s.pe === null) return false
    if (Number(s.pe) > f.maxPe) return false
  }
  if (f.search.trim()) {
    const q = f.search.toLowerCase()
    if (!s.ticker.toLowerCase().includes(q) && !s.name.toLowerCase().includes(q)) {
      return false
    }
  }
  return true
}
