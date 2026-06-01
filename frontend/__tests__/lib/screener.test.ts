import { matchesFilters } from '@/lib/screener'
import type { StockListItem } from '@/lib/types'

const base: StockListItem = {
  ticker: 'GP', name: 'Grameenphone', sector: 'Telecom', category: 'A',
  market_cap_bdt: 1000, is_active: true, pe: 12, health_score: 82,
  last_close: 300, change_pct: 1, rating: 'STRONG_BUY',
}

describe('matchesFilters', () => {
  it('passes when all filters are at defaults', () => {
    expect(matchesFilters(base, { sector: 'All', search: '', maxPe: null, rating: 'All' })).toBe(true)
  })
  it('rejects when pe exceeds maxPe', () => {
    expect(matchesFilters(base, { sector: 'All', search: '', maxPe: 10, rating: 'All' })).toBe(false)
  })
  it('keeps rows with null pe only when maxPe is unset', () => {
    const noPe = { ...base, pe: null }
    expect(matchesFilters(noPe, { sector: 'All', search: '', maxPe: 10, rating: 'All' })).toBe(false)
    expect(matchesFilters(noPe, { sector: 'All', search: '', maxPe: null, rating: 'All' })).toBe(true)
  })
  it('filters by exact rating', () => {
    expect(matchesFilters(base, { sector: 'All', search: '', maxPe: null, rating: 'BUY' })).toBe(false)
    expect(matchesFilters(base, { sector: 'All', search: '', maxPe: null, rating: 'STRONG_BUY' })).toBe(true)
  })
  it('matches search against ticker and name, case-insensitively', () => {
    expect(matchesFilters(base, { sector: 'All', search: 'grameen', maxPe: null, rating: 'All' })).toBe(true)
    expect(matchesFilters(base, { sector: 'All', search: 'xyz', maxPe: null, rating: 'All' })).toBe(false)
  })
})
