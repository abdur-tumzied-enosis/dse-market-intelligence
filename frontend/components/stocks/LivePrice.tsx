'use client'

import { useEffect, useState } from 'react'
import { get } from '@/lib/api'
import type { LatestPrice, LivePrice as LivePriceType } from '@/lib/types'

function fmtBDT(n: number | null | undefined): string {
  if (n == null) return '—'
  return `৳${Number(n).toFixed(2)}`
}
function fmtCap(bdt: number | null | undefined): string {
  if (!bdt) return '—'
  const cr = Number(bdt) / 10_000_000
  if (cr >= 1000) return `৳${(cr / 1000).toFixed(1)}K Cr`
  return `৳${cr.toFixed(0)} Cr`
}
function fmtVol(v: number | null | undefined): string {
  const n = Number(v)
  if (!n) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

const POLL_MS = 120_000

function seed(initial: LatestPrice | null): LivePriceType {
  if (!initial) {
    return {
      ticker: '', available: false, ltp: null, high: null, low: null,
      prev_close: null, change_pct: null, volume: null, value_bdt: null,
      market_status: 'Closed', status_source: undefined, as_of: new Date().toISOString(),
    }
  }
  return {
    ticker: '', available: true,
    ltp: initial.close != null ? Number(initial.close) : null,
    high: initial.high != null ? Number(initial.high) : null,
    low: initial.low != null ? Number(initial.low) : null,
    prev_close: null,
    change_pct: initial.change_pct != null ? Number(initial.change_pct) : null,
    volume: initial.volume != null ? Number(initial.volume) : null,
    value_bdt: initial.value_bdt != null ? Number(initial.value_bdt) : null,
    market_status: 'Open',
    as_of: initial.time ?? new Date().toISOString(),
  }
}

// ─── Day range bar ─────────────────────────────────────────────────────────
// Low ——•—— High track with the current price as a glowing marker and the
// previous close (when known) as a faint tick. Degenerates gracefully when
// high == low or either bound is missing.
function DayRangeBar({
  low,
  high,
  ltp,
  prevClose,
  up,
}: {
  low: number | null
  high: number | null
  ltp: number | null
  prevClose: number | null
  up: boolean
}) {
  if (low == null || high == null || ltp == null) return null
  const span = high - low
  const clamp = (v: number) => Math.max(0, Math.min(100, v))
  const pos = span > 0 ? clamp(((ltp - low) / span) * 100) : 50
  const prevPos =
    prevClose != null && span > 0 ? clamp(((prevClose - low) / span) * 100) : null
  const accent = up ? '#00d4a4' : '#ff4d6a'

  return (
    <div className="mt-3 w-full">
      <div className="flex items-end justify-between mb-1">
        <span className="text-[9px] font-mono uppercase tracking-[0.18em] text-[#6b6b80]">
          Day Range
        </span>
        {prevClose != null && (
          <span className="text-[9px] font-mono text-[#6b6b80]">
            prev <span className="text-[#9b9bb0] tabular-nums">{fmtBDT(prevClose)}</span>
          </span>
        )}
      </div>
      <div className="relative h-1.5 rounded-full bg-[#1a1a24] overflow-visible">
        {/* filled portion from low up to current price */}
        <div
          className="absolute inset-y-0 left-0 rounded-full"
          style={{ width: `${pos}%`, background: `linear-gradient(90deg, ${accent}33, ${accent})` }}
        />
        {/* previous close tick */}
        {prevPos != null && (
          <div
            className="absolute top-1/2 h-3 w-px -translate-y-1/2 bg-[#6b6b80]"
            style={{ left: `${prevPos}%` }}
            title={`Prev close ${fmtBDT(prevClose)}`}
          />
        )}
        {/* current price marker */}
        <div
          className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-[#0a0a0f]"
          style={{ left: `${pos}%`, background: accent, boxShadow: `0 0 8px ${accent}99` }}
        />
      </div>
      <div className="flex justify-between mt-1">
        <span className="text-[10px] font-mono tabular-nums text-[#ff4d6a]">{fmtBDT(low)}</span>
        <span className="text-[10px] font-mono tabular-nums text-[#00d4a4]">{fmtBDT(high)}</span>
      </div>
    </div>
  )
}

export default function LivePrice({
  ticker,
  initial,
}: {
  ticker: string
  initial: LatestPrice | null
}) {
  const [data, setData] = useState<LivePriceType>(() => seed(initial))

  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setInterval> | null = null

    async function tick() {
      try {
        const r = await get<LivePriceType>(`/api/stocks/${ticker}/live`)
        if (!alive) return
        if (r.available) setData(r)
        else setData(d => ({ ...d, market_status: r.market_status, as_of: r.as_of }))
        if (r.market_status !== 'Open' && timer) {
          clearInterval(timer)
          timer = null
        }
      } catch {
        /* keep last known values */
      }
    }

    tick()
    timer = setInterval(tick, POLL_MS)
    return () => {
      alive = false
      if (timer) clearInterval(timer)
    }
  }, [ticker])

  const price = data.ltp
  const changePct = data.change_pct ?? 0
  const isUp = changePct >= 0
  const isLive = data.market_status === 'Open'
  const estimated = !!data.status_source && data.status_source !== 'dse_direct'
  const accent = isUp ? '#00d4a4' : '#ff4d6a'
  const asOf = new Date(data.as_of).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Asia/Dhaka',
  })

  return (
    <div className="flex flex-col items-start sm:items-end gap-1 min-w-[260px]">
      {/* Price + change */}
      <div className="flex items-baseline gap-3">
        <span className="text-[44px] font-mono font-bold tabular-nums leading-none text-white">
          {price != null ? `৳${price.toFixed(2)}` : '—'}
        </span>
        {price != null && (
          <span
            className="inline-flex items-center gap-1 text-[15px] font-mono font-semibold tabular-nums rounded-md px-2 py-0.5"
            style={{ color: accent, backgroundColor: `${accent}1a` }}
          >
            <span aria-hidden>{isUp ? '▲' : '▼'}</span>
            {isUp ? '+' : ''}
            {changePct.toFixed(2)}%
          </span>
        )}
      </div>

      {/* Live status */}
      <div className="flex items-center gap-1.5">
        <span
          className={`w-1.5 h-1.5 rounded-full ${isLive ? 'bg-[#00d4a4] animate-pulse' : 'bg-[#6b6b80]'}`}
        />
        <span className="text-[9px] font-mono uppercase tracking-[0.18em] text-[#6b6b80]">
          {isLive ? `live · as of ${asOf}` : 'market closed'}
          {estimated ? ' (est.)' : ''}
        </span>
      </div>

      {/* OHLV stat strip */}
      {price != null && (
        <div className="flex gap-5 mt-2">
          {[
            { l: 'HIGH', v: fmtBDT(data.high), c: '#00d4a4' },
            { l: 'LOW', v: fmtBDT(data.low), c: '#ff4d6a' },
            { l: 'VOLUME', v: fmtVol(data.volume), c: null },
            { l: 'VALUE', v: fmtCap(data.value_bdt), c: null },
          ].map(({ l, v, c }) => (
            <div key={l} className="text-left sm:text-right">
              <div className="text-[9px] font-mono uppercase tracking-[0.2em] text-[#6b6b80] mb-0.5">
                {l}
              </div>
              <div
                className="text-sm font-mono font-medium tabular-nums"
                style={{ color: c ?? '#e8e8f0' }}
              >
                {v}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Day range */}
      {price != null && (
        <DayRangeBar
          low={data.low}
          high={data.high}
          ltp={price}
          prevClose={data.prev_close}
          up={isUp}
        />
      )}
    </div>
  )
}
