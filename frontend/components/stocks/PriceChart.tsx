'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import { get } from '@/lib/api'
import type { OHLCVResponse } from '@/lib/types'

const RANGES = [
  { label: '1M', days: 30 },
  { label: '3M', days: 90 },
  { label: '6M', days: 180 },
  { label: '1Y', days: 365 },
  { label: '3Y', days: 1095 },
  { label: 'ALL', days: 0 },
]

interface HoveredCandle {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export default function PriceChart({ ticker }: { ticker: string }) {
  const containerRef = useRef<HTMLDivElement>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chartRef  = useRef<any>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const candleRef = useRef<any>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const volRef    = useRef<any>(null)

  const [activeRange, setActiveRange] = useState('1Y')
  const [loading, setLoading]         = useState(false)
  const [error, setError]             = useState<string | null>(null)
  const [hovered, setHovered]         = useState<HoveredCandle | null>(null)

  const loadData = useCallback(async (range: string) => {
    if (!candleRef.current || !volRef.current) return
    setLoading(true)
    setError(null)
    try {
      const r = RANGES.find(x => x.label === range)!
      let qs = 'interval=daily'
      if (r.days > 0) {
        const from = new Date(Date.now() - r.days * 86400000).toISOString().slice(0, 10)
        qs += `&from=${from}`
      }
      const resp = await get<OHLCVResponse>(`/api/stocks/${ticker}/prices?${qs}`)
      const sorted = [...resp.items].filter(d => d.close != null).reverse()

      candleRef.current.setData(sorted.map(d => ({
        time:  d.day,
        open:  Number(d.open  ?? d.close),
        high:  Number(d.high  ?? d.close),
        low:   Number(d.low   ?? d.close),
        close: Number(d.close),
      })))

      volRef.current.setData(sorted.map(d => ({
        time:  d.day,
        value: Number(d.volume ?? 0),
        color: Number(d.close) >= Number(d.open ?? d.close)
          ? 'rgba(0,212,164,0.22)'
          : 'rgba(255,77,106,0.22)',
      })))

      chartRef.current?.timeScale().fitContent()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [ticker])

  // Init chart — recreate when ticker changes
  useEffect(() => {
    if (!containerRef.current) return

    let destroyed = false

    import('lightweight-charts').then(lc => {
      if (destroyed || !containerRef.current) return

      const chart = lc.createChart(containerRef.current, {
        autoSize: true,
        layout: {
          background: { type: lc.ColorType.Solid, color: '#111118' },
          textColor: '#6b6b80',
          fontFamily: "'Menlo', 'Consolas', monospace",
          fontSize: 10,
        },
        grid: {
          vertLines: { color: '#252535' },
          horzLines: { color: '#252535' },
        },
        crosshair: {
          mode: lc.CrosshairMode.Normal,
          vertLine: { color: '#4d9eff', labelBackgroundColor: '#4d9eff' },
          horzLine: { color: '#4d9eff', labelBackgroundColor: '#4d9eff' },
        },
        rightPriceScale: {
          borderColor: '#2a2a3a',
          textColor: '#6b6b80',
        },
        timeScale: {
          borderColor: '#2a2a3a',
          timeVisible: true,
          secondsVisible: false,
          rightOffset: 5,
        },
        handleScroll: true,
        handleScale: true,
      })

      const candleSeries = chart.addSeries(lc.CandlestickSeries, {
        upColor:        '#00d4a4',
        downColor:      '#ff4d6a',
        borderUpColor:  '#00d4a4',
        borderDownColor:'#ff4d6a',
        wickUpColor:    '#00d4a4',
        wickDownColor:  '#ff4d6a',
      })

      const volSeries = chart.addSeries(lc.HistogramSeries, {
        color:             'rgba(0,212,164,0.22)',
        priceFormat:       { type: 'volume' },
        priceScaleId:      'vol',
        lastValueVisible:  false,
        priceLineVisible:  false,
      })
      chart.priceScale('vol').applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      })

      // OHLCV hover strip
      chart.subscribeCrosshairMove((param: any) => {
        if (!param.time || !param.point) { setHovered(null); return }
        const c = param.seriesData?.get(candleSeries)
        const v = param.seriesData?.get(volSeries)
        if (c) {
          setHovered({
            time:   String(param.time),
            open:   c.open,
            high:   c.high,
            low:    c.low,
            close:  c.close,
            volume: v?.value ?? 0,
          })
        } else {
          setHovered(null)
        }
      })

      chartRef.current  = chart
      candleRef.current = candleSeries
      volRef.current    = volSeries

      loadData('1Y')
    })

    return () => {
      destroyed = true
      chartRef.current?.remove()
      chartRef.current  = null
      candleRef.current = null
      volRef.current    = null
      setActiveRange('1Y')
      setHovered(null)
    }
  }, [ticker, loadData])

  // Range button → reload data
  useEffect(() => {
    loadData(activeRange)
  }, [activeRange, loadData])

  return (
    <div className="flex flex-col gap-3 h-full">
      {/* Controls row */}
      <div className="flex items-center justify-between min-h-[26px]">
        <div className="flex gap-1">
          {RANGES.map(r => (
            <button
              key={r.label}
              onClick={() => setActiveRange(r.label)}
              className={`px-2.5 py-0.5 text-[11px] font-mono rounded transition-all ${
                activeRange === r.label
                  ? 'bg-[#4d9eff]/15 text-[#4d9eff] border border-[#4d9eff]/30'
                  : 'text-[#6b6b80] hover:text-[#e8e8f0] border border-transparent hover:border-[#2a2a3a]'
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>

        {hovered ? (
          <div className="flex gap-3 text-[10px] font-mono">
            {[
              { l: 'O', v: hovered.open.toFixed(1) },
              { l: 'H', v: hovered.high.toFixed(1),  c: '#00d4a4' },
              { l: 'L', v: hovered.low.toFixed(1),   c: '#ff4d6a' },
              { l: 'C', v: hovered.close.toFixed(1) },
              {
                l: 'V',
                v: hovered.volume >= 1e6
                  ? `${(hovered.volume / 1e6).toFixed(2)}M`
                  : `${(hovered.volume / 1e3).toFixed(0)}K`,
              },
            ].map(({ l, v, c }) => (
              <span key={l}>
                <span className="text-[#6b6b80]">{l}: </span>
                <span style={{ color: c ?? '#e8e8f0' }}>{v}</span>
              </span>
            ))}
            <span className="text-[#6b6b80] ml-1">{hovered.time}</span>
          </div>
        ) : null}
      </div>

      {/* Chart container — lightweight-charts mounts here */}
      <div ref={containerRef} className="flex-1 relative">
        {loading && (
          <div className="absolute top-2 right-10 z-10 flex items-center gap-1.5 pointer-events-none">
            <div className="w-1.5 h-1.5 rounded-full bg-[#4d9eff] animate-pulse" />
            <span className="text-[10px] font-mono text-[#6b6b80]">loading</span>
          </div>
        )}
        {error && (
          <div className="absolute inset-0 flex items-center justify-center z-10 pointer-events-none">
            <span className="text-sm font-mono text-[#ff4d6a]">{error}</span>
          </div>
        )}
      </div>
    </div>
  )
}
