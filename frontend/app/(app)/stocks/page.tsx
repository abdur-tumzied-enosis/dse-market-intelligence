'use client'

import { useEffect, useState, useMemo } from 'react'
import Link from 'next/link'
import { get } from '@/lib/api'
import type { PagedResponse, StockListItem } from '@/lib/types'
import { matchesFilters } from '@/lib/screener'
import RatingBadge from '@/components/stocks/RatingBadge'

const SECTORS = [
  'All', 'Bank', 'Insurance', 'Financial Institutions', 'Telecom',
  'Pharmaceuticals & Chemicals', 'Engineering', 'Textile',
  'Food & Allied', 'Cement', 'Fuel & Power', 'IT',
  'Miscellaneous', 'Service & Real Estate', 'Jute', 'Paper & Printing',
  'Ceramics', 'Tannery', 'Travel & Leisure', 'Corporate Bond',
]

function fmtCap(bdt: number | null): string {
  if (!bdt) return '—'
  const cr = bdt / 10_000_000
  if (cr >= 1000) return `৳${(cr / 1000).toFixed(1)}K Cr`
  return `৳${cr.toFixed(0)} Cr`
}

type SortKey = 'name' | 'ticker' | 'sector' | 'market_cap_bdt' | 'pe'

export default function StocksPage() {
  const [stocks, setStocks] = useState<StockListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [sector, setSector] = useState('All')
  const [maxPe, setMaxPe] = useState<number | null>(null)
  const [rating, setRating] = useState('All')
  const RATINGS = ['All', 'STRONG_BUY', 'BUY', 'HOLD', 'SELL', 'STRONG_SELL']
  const [sortKey, setSortKey] = useState<SortKey>('market_cap_bdt')
  const [sortAsc, setSortAsc] = useState(false)

  useEffect(() => {
    setLoading(true)
    async function loadAll() {
      const first = await get<PagedResponse<StockListItem>>('/api/stocks?limit=200&offset=0')
      let items = first.items
      if (first.total > 200) {
        const rest = await Promise.all(
          Array.from({ length: Math.ceil((first.total - 200) / 200) }, (_, i) =>
            get<PagedResponse<StockListItem>>(`/api/stocks?limit=200&offset=${200 + i * 200}`)
          )
        )
        items = items.concat(rest.flatMap(r => r.items))
      }
      return items
    }
    loadAll()
      .then(items => setStocks(items))
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [])

  const filtered = useMemo(() => {
    const s = stocks.filter(x => matchesFilters(x, { sector, search, maxPe, rating }))
    return [...s].sort((a, b) => {
      let av: string | number = a[sortKey] ?? ''
      let bv: string | number = b[sortKey] ?? ''
      if (typeof av === 'string') av = av.toLowerCase()
      if (typeof bv === 'string') bv = bv.toLowerCase()
      if (av < bv) return sortAsc ? -1 : 1
      if (av > bv) return sortAsc ? 1 : -1
      return 0
    })
  }, [stocks, sector, search, maxPe, rating, sortKey, sortAsc])

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortAsc(p => !p)
    else { setSortKey(key); setSortAsc(key !== 'market_cap_bdt') }
  }

  function SortIcon({ k }: { k: SortKey }) {
    if (sortKey !== k) return <span className="text-[#3a3a4a] ml-1">⇅</span>
    return <span className="text-[#4d9eff] ml-1">{sortAsc ? '↑' : '↓'}</span>
  }

  return (
    <div className="space-y-5 max-w-[1400px]">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Stock Screener</h1>
          <p className="text-[#6b6b80] text-sm font-mono mt-0.5">
            {loading ? 'Loading...' : `${filtered.length} of ${stocks.length} companies`}
          </p>
        </div>
      </div>

      {/* Filters */}
      <div className="bg-[#111118] border border-[#2a2a3a] rounded-xl p-4 flex flex-wrap gap-3 items-center">
        <input
          type="text"
          placeholder="Search ticker or name…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="flex-1 min-w-[200px] bg-[#1a1a24] border border-[#2a2a3a] rounded-lg px-3 py-2 text-sm font-mono text-[#e8e8f0] placeholder:text-[#6b6b80] focus:outline-none focus:border-[#4d9eff]/50 transition-colors"
        />
        <select
          value={sector}
          onChange={e => setSector(e.target.value)}
          className="bg-[#1a1a24] border border-[#2a2a3a] rounded-lg px-3 py-2 text-sm font-mono text-[#e8e8f0] focus:outline-none focus:border-[#4d9eff]/50 transition-colors"
        >
          {SECTORS.map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <input
          type="number"
          placeholder="Max P/E"
          value={maxPe ?? ''}
          onChange={e => setMaxPe(e.target.value === '' ? null : Number(e.target.value))}
          className="w-28 bg-[#1a1a24] border border-[#2a2a3a] rounded-lg px-3 py-2 text-sm font-mono text-[#e8e8f0] placeholder:text-[#6b6b80] focus:outline-none focus:border-[#4d9eff]/50 transition-colors"
        />
        <select
          value={rating}
          onChange={e => setRating(e.target.value)}
          className="bg-[#1a1a24] border border-[#2a2a3a] rounded-lg px-3 py-2 text-sm font-mono text-[#e8e8f0] focus:outline-none focus:border-[#4d9eff]/50 transition-colors"
        >
          {RATINGS.map(r => <option key={r} value={r}>{r === 'All' ? 'All ratings' : r.replace('_', ' ')}</option>)}
        </select>
        {(search || sector !== 'All' || maxPe !== null || rating !== 'All') && (
          <button
            onClick={() => { setSearch(''); setSector('All'); setMaxPe(null); setRating('All') }}
            className="text-[11px] font-mono text-[#6b6b80] hover:text-[#ff4d6a] transition-colors px-2 py-1 border border-[#2a2a3a] rounded-lg"
          >
            Clear
          </button>
        )}
      </div>

      {/* Table */}
      <div className="bg-[#111118] border border-[#2a2a3a] rounded-xl overflow-hidden">
        {error ? (
          <div className="py-16 text-center text-[#ff4d6a] font-mono text-sm">{error}</div>
        ) : loading ? (
          <div className="py-16 text-center">
            <div className="inline-flex items-center gap-2 text-[#6b6b80] font-mono text-sm">
              <div className="w-2 h-2 rounded-full bg-[#4d9eff] animate-pulse" />
              Loading stocks…
            </div>
          </div>
        ) : filtered.length === 0 ? (
          <div className="py-16 text-center text-[#6b6b80] font-mono text-sm">No stocks match your filter</div>
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-[#2a2a3a]">
                <th className="py-3 pl-4 pr-2 text-left w-10 text-[10px] font-mono uppercase tracking-wider text-[#6b6b80]">#</th>
                <th className="py-3 px-2 text-left cursor-pointer select-none"
                  onClick={() => toggleSort('ticker')}>
                  <span className="text-[10px] font-mono uppercase tracking-wider text-[#6b6b80] hover:text-[#e8e8f0] transition-colors">
                    Ticker<SortIcon k="ticker" />
                  </span>
                </th>
                <th className="py-3 px-2 text-left cursor-pointer select-none"
                  onClick={() => toggleSort('name')}>
                  <span className="text-[10px] font-mono uppercase tracking-wider text-[#6b6b80] hover:text-[#e8e8f0] transition-colors">
                    Company<SortIcon k="name" />
                  </span>
                </th>
                <th className="py-3 px-2 text-left hidden md:table-cell cursor-pointer select-none"
                  onClick={() => toggleSort('sector')}>
                  <span className="text-[10px] font-mono uppercase tracking-wider text-[#6b6b80] hover:text-[#e8e8f0] transition-colors">
                    Sector<SortIcon k="sector" />
                  </span>
                </th>
                <th className="py-3 px-2 text-left hidden sm:table-cell">
                  <span className="text-[10px] font-mono uppercase tracking-wider text-[#6b6b80]">Category</span>
                </th>
                <th className="py-3 px-2 text-right cursor-pointer select-none"
                  onClick={() => toggleSort('market_cap_bdt')}>
                  <span className="text-[10px] font-mono uppercase tracking-wider text-[#6b6b80] hover:text-[#e8e8f0] transition-colors">
                    Market Cap<SortIcon k="market_cap_bdt" />
                  </span>
                </th>
                <th className="py-3 px-2 text-right hidden sm:table-cell cursor-pointer select-none"
                  onClick={() => toggleSort('pe')}>
                  <span className="text-[10px] font-mono uppercase tracking-wider text-[#6b6b80] hover:text-[#e8e8f0] transition-colors">
                    P/E{sortKey === 'pe' ? <span className="text-[#4d9eff] ml-1">{sortAsc ? '↑' : '↓'}</span> : <span className="text-[#3a3a4a] ml-1">⇅</span>}
                  </span>
                </th>
                <th className="py-3 px-2 text-right hidden md:table-cell">
                  <span className="text-[10px] font-mono uppercase tracking-wider text-[#6b6b80]">Rating</span>
                </th>
                <th className="py-3 pl-2 pr-4 w-14" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((stock, i) => (
                <tr key={stock.ticker}
                  className="border-b border-[#1a1a24] hover:bg-[#0f0f18] transition-colors group">
                  <td className="py-3 pl-4 pr-2 text-[#6b6b80] text-[11px] font-mono">{i + 1}</td>
                  <td className="py-3 px-2">
                    <Link href={`/stocks/${stock.ticker}`}
                      className="font-mono font-bold text-sm text-[#4d9eff] hover:text-[#7ab8ff] transition-colors tracking-wide">
                      {stock.ticker}
                    </Link>
                  </td>
                  <td className="py-3 px-2">
                    <Link href={`/stocks/${stock.ticker}`}
                      className="text-sm text-[#e8e8f0] group-hover:text-white transition-colors">
                      <span className="block max-w-[260px] truncate">{stock.name}</span>
                    </Link>
                  </td>
                  <td className="py-3 px-2 hidden md:table-cell">
                    <span className="text-[11px] font-mono text-[#6b6b80] bg-[#1a1a24] px-2 py-0.5 rounded border border-[#2a2a3a]">
                      {stock.sector}
                    </span>
                  </td>
                  <td className="py-3 px-2 hidden sm:table-cell">
                    <span className="text-[11px] font-mono text-[#6b6b80]">{stock.category ?? '—'}</span>
                  </td>
                  <td className="py-3 px-2 text-right">
                    <span className="text-sm font-mono tabular-nums text-[#e8e8f0]">
                      {fmtCap(stock.market_cap_bdt)}
                    </span>
                  </td>
                  <td className="py-3 px-2 text-right hidden sm:table-cell">
                    <span className="text-sm font-mono tabular-nums text-[#e8e8f0]">
                      {stock.pe != null ? Number(stock.pe).toFixed(1) : '—'}
                    </span>
                  </td>
                  <td className="py-3 px-2 text-right hidden md:table-cell">
                    <RatingBadge score={stock.health_score} />
                  </td>
                  <td className="py-3 pl-2 pr-4 text-right">
                    <Link href={`/stocks/${stock.ticker}`}
                      className="text-[11px] font-mono text-[#4d9eff] hover:text-[#7ab8ff] opacity-0 group-hover:opacity-100 transition-opacity">
                      View →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
