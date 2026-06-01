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
      market_status: 'Closed', as_of: new Date().toISOString(),
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
  const asOf = new Date(data.as_of).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
  })

  return (
    <div className="flex flex-wrap items-start justify-between gap-4 w-full">
      <div>
        <div className="flex items-baseline gap-3">
          <span className="text-[42px] font-mono font-bold tabular-nums leading-none text-white">
            {price != null ? `৳${price.toFixed(2)}` : '—'}
          </span>
          {price != null && (
            <span
              className="text-[18px] font-mono font-semibold tabular-nums"
              style={{ color: isUp ? '#00d4a4' : '#ff4d6a' }}
            >
              {isUp ? '+' : ''}
              {changePct.toFixed(2)}%
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5 mt-1.5">
          <span
            className={`w-1.5 h-1.5 rounded-full ${isLive ? 'bg-[#00d4a4] animate-pulse' : 'bg-[#6b6b80]'}`}
          />
          <span className="text-[9px] font-mono uppercase tracking-[0.18em] text-[#6b6b80]">
            {isLive ? `live · as of ${asOf}` : 'market closed'}
          </span>
        </div>
      </div>

      {price != null && (
        <div className="flex gap-5 self-end pb-1">
          {[
            { l: 'HIGH', v: fmtBDT(data.high), c: '#00d4a4' },
            { l: 'LOW', v: fmtBDT(data.low), c: '#ff4d6a' },
            { l: 'VOLUME', v: fmtVol(data.volume), c: null },
            { l: 'VALUE', v: fmtCap(data.value_bdt), c: null },
          ].map(({ l, v, c }) => (
            <div key={l} className="text-center">
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
    </div>
  )
}
