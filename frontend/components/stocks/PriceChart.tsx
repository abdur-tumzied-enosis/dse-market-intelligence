'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import { get } from '@/lib/api'
import { computeVolumeProfile } from '@/lib/volumeProfile'
import type { VPCandle, VProfile } from '@/lib/volumeProfile'
import type {
  OHLCVResponse,
  LivePrice,
  WyckoffResponse,
  WyckoffRange,
  WyckoffEventType,
} from '@/lib/types'

const RANGES = [
  { label: '1M', days: 30 },
  { label: '3M', days: 90 },
  { label: '4M', days: 120 },
  { label: '6M', days: 180 },
  { label: '1Y', days: 365 },
  { label: '3Y', days: 1095 },
  { label: 'ALL', days: 0 },
]

// Palette (mirrors candle colors)
const UP = '#00d4a4'
const DOWN = '#ff4d6a'
const NEUTRAL = '#a78bfa'

// Volume Profile palette + layout
const VP_BINS = 24
const VP_COLOR = '#7aa2f7'          // soft blue — distinct from candle green/red + Wyckoff violet
const VP_POC_COLOR = '#ffd166'      // amber POC line
const VP_BAR_MAX_FRACTION = 0.35    // widest bar = 35% of chart width
const VP_BAR_ALPHA = 0.22           // out-of-value-area bars
const VP_VA_ALPHA = 0.34            // value-area bars (slightly stronger)

// Per-event-type marker styling, grouped by Wyckoff side:
//  • Markup / bullish (PS, SC, SPRING, TEST, SOS, LPS): green, arrow below the bar.
//  • Markdown / bearish (PSY, BC, UT, UTAD, SOW, LPSY): red, arrow above the bar.
//  • Neutral structural (AR, ST): violet circle.
const EVENT_STYLE: Record<
  WyckoffEventType,
  { color: string; position: 'aboveBar' | 'belowBar'; shape: 'arrowUp' | 'arrowDown' | 'circle' }
> = {
  // Markup / bullish side — green, arrow below the bar
  PS:     { color: UP,      position: 'belowBar', shape: 'arrowUp' },
  SC:     { color: UP,      position: 'belowBar', shape: 'arrowUp' },
  SPRING: { color: UP,      position: 'belowBar', shape: 'arrowUp' },
  TEST:   { color: UP,      position: 'belowBar', shape: 'arrowUp' },
  SOS:    { color: UP,      position: 'belowBar', shape: 'arrowUp' },
  LPS:    { color: UP,      position: 'belowBar', shape: 'arrowUp' },
  // Markdown / bearish side — red, arrow above the bar
  PSY:    { color: DOWN,    position: 'aboveBar', shape: 'arrowDown' },
  BC:     { color: DOWN,    position: 'aboveBar', shape: 'arrowDown' },
  UT:     { color: DOWN,    position: 'aboveBar', shape: 'arrowDown' },
  UTAD:   { color: DOWN,    position: 'aboveBar', shape: 'arrowDown' },
  SOW:    { color: DOWN,    position: 'aboveBar', shape: 'arrowDown' },
  LPSY:   { color: DOWN,    position: 'aboveBar', shape: 'arrowDown' },
  // Neutral structural — violet circle
  AR:     { color: NEUTRAL, position: 'aboveBar', shape: 'circle' },
  ST:     { color: NEUTRAL, position: 'aboveBar', shape: 'circle' },
}

const PHASE_COLOR: Record<string, string> = {
  accumulation: UP,
  distribution: DOWN,
  undetermined: NEUTRAL,
}

interface HoveredCandle {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

// A small floating tooltip describing the Wyckoff event under the crosshair.
interface EventTip {
  label: string
  help: string
}

function fmtShortDate(iso: string): string {
  // "2026-03-12" → "12 Mar"
  const d = new Date(iso + 'T00:00:00')
  if (Number.isNaN(d.getTime())) return iso
  return new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short' }).format(d)
}

export default function PriceChart({ ticker }: { ticker: string }) {
  const containerRef = useRef<HTMLDivElement>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chartRef  = useRef<any>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const candleRef = useRef<any>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const volRef    = useRef<any>(null)

  // Volume Profile handles + cached candle data (series doesn't expose data back)
  const vpRef     = useRef<VolumeProfilePrimitive | null>(null)
  const dataRef   = useRef<Array<VPCandle & { time: string }>>([])
  const vpRafRef  = useRef<number | null>(null)

  // Wyckoff overlay handles, kept in refs so cleanup is independent of React state.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const markersRef   = useRef<any>(null)              // ISeriesMarkersPluginApi
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const priceLinesRef = useRef<any[]>([])             // IPriceLine[]
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const primitivesRef = useRef<any[]>([])             // PhaseBoxPrimitive[]
  // help text per ISO day, for the crosshair tooltip
  const eventHelpRef  = useRef<Record<string, EventTip>>({})

  const [activeRange, setActiveRange]   = useState('1Y')
  const [loading, setLoading]           = useState(false)
  const [error, setError]               = useState<string | null>(null)
  const [hovered, setHovered]           = useState<HoveredCandle | null>(null)
  const [eventTip, setEventTip]         = useState<EventTip | null>(null)

  // Wyckoff state
  const [wyckoffOn, setWyckoffOn]       = useState(false)
  const wyckoffOnRef                    = useRef(false)
  const [wyckoffRanges, setWyckoffRanges] = useState<WyckoffRange[]>([])
  const [wyckoffBusy, setWyckoffBusy]   = useState(false)

  // Volume Profile state
  const [vpOn, setVpOn] = useState(false)
  const vpOnRef         = useRef(false)

  // Recompute the volume profile from the candles inside the current visible
  // logical range and push it into the primitive. No-op unless VP is on.
  const recomputeVP = useCallback(() => {
    const chart = chartRef.current
    const vp = vpRef.current
    if (!vpOnRef.current || !chart || !vp) return
    const all = dataRef.current
    let visible = all
    const lr = chart.timeScale().getVisibleLogicalRange()
    if (lr) {
      const from = Math.max(0, Math.floor(lr.from))
      const to = Math.min(all.length - 1, Math.ceil(lr.to))
      visible = from <= to ? all.slice(from, to + 1) : []
    }
    vp.setProfile(computeVolumeProfile(visible, VP_BINS))
  }, [])

  // Coalesce a burst of pan/zoom events into a single recompute per frame.
  const scheduleVP = useCallback(() => {
    if (!vpOnRef.current) return
    if (vpRafRef.current != null) cancelAnimationFrame(vpRafRef.current)
    vpRafRef.current = requestAnimationFrame(() => {
      vpRafRef.current = null
      recomputeVP()
    })
  }, [recomputeVP])

  // Compute the same `from` date the chart uses for a given range label.
  const fromForRange = useCallback((range: string): string | null => {
    const r = RANGES.find(x => x.label === range)!
    if (r.days <= 0) return null
    return new Date(Date.now() - r.days * 86400000).toISOString().slice(0, 10)
  }, [])

  // Remove every Wyckoff overlay (markers, price lines, phase boxes) and reset
  // the help lookup. Safe to call when nothing is drawn.
  const clearWyckoff = useCallback(() => {
    if (markersRef.current) {
      try { markersRef.current.setMarkers([]) } catch { /* detached */ }
    }
    if (candleRef.current) {
      for (const pl of priceLinesRef.current) {
        try { candleRef.current.removePriceLine(pl) } catch { /* gone */ }
      }
      for (const prim of primitivesRef.current) {
        try { candleRef.current.detachPrimitive(prim) } catch { /* gone */ }
      }
    }
    priceLinesRef.current = []
    primitivesRef.current = []
    eventHelpRef.current = {}
    setWyckoffRanges([])
    setEventTip(null)
  }, [])

  // Draw all overlays for the supplied ranges onto the candle series.
  const drawWyckoff = useCallback((ranges: WyckoffRange[]) => {
    const candle = candleRef.current
    const lc = lcModRef.current
    if (!candle || !lc) return

    clearWyckoff()

    // 1) Event markers + hover help index
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const markers: any[] = []
    const help: Record<string, EventTip> = {}
    for (const rg of ranges) {
      for (const ev of rg.events) {
        const style = EVENT_STYLE[ev.type as WyckoffEventType] ?? {
          color: NEUTRAL, position: 'aboveBar' as const, shape: 'circle' as const,
        }
        markers.push({
          time: ev.day,
          position: style.position,
          color: style.color,
          shape: style.shape,
          text: ev.label,
        })
        help[ev.day] = { label: ev.label, help: ev.help }
      }
    }
    markers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0))
    eventHelpRef.current = help
    if (markersRef.current) {
      markersRef.current.setMarkers(markers)
    } else {
      markersRef.current = lc.createSeriesMarkers(candle, markers)
    }

    // 2) Support / Resistance horizontal lines (per range)
    for (const rg of ranges) {
      const phaseColor = PHASE_COLOR[rg.phase] ?? NEUTRAL
      priceLinesRef.current.push(candle.createPriceLine({
        price: Number(rg.support),
        color: '#00d4a4',
        lineWidth: 1,
        lineStyle: lc.LineStyle.Dashed,
        axisLabelVisible: true,
        title: 'S',
      }))
      priceLinesRef.current.push(candle.createPriceLine({
        price: Number(rg.resistance),
        color: '#ff4d6a',
        lineWidth: 1,
        lineStyle: lc.LineStyle.Dashed,
        axisLabelVisible: true,
        title: 'R',
      }))

      // 3) Phase box primitive
      const prim = new PhaseBoxPrimitive({
        startDay: rg.start_day,
        endDay: rg.end_day,
        support: Number(rg.support),
        resistance: Number(rg.resistance),
        color: phaseColor,
        label: rg.phase,
      })
      candle.attachPrimitive(prim)
      primitivesRef.current.push(prim)
    }

    setWyckoffRanges(ranges)
  }, [clearWyckoff])

  // Fetch Wyckoff analysis for the active range and draw it.
  const loadWyckoff = useCallback(async (range: string) => {
    if (!candleRef.current || !lcModRef.current) return
    setWyckoffBusy(true)
    setError(null)
    try {
      let qs = 'interval=daily'
      const from = fromForRange(range)
      if (from) qs += `&from=${from}`
      const resp = await get<WyckoffResponse>(`/api/stocks/${ticker}/wyckoff?${qs}`)
      // Only draw if still toggled on (avoids a late response re-adding overlays).
      if (wyckoffOnRef.current) drawWyckoff(resp.ranges ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load Wyckoff analysis')
    } finally {
      setWyckoffBusy(false)
    }
  }, [ticker, fromForRange, drawWyckoff])

  // Append/refresh today's candle from the live endpoint. daily_ohlcv lags
  // (hourly continuous-aggregate + EOD), so today's bar is sourced live here.
  // Live feed has no open → use prev_close (change-since-prev-close candle).
  // Returns the market_status so the caller can stop polling when closed.
  const appendLiveBar = useCallback(async (): Promise<string | null> => {
    if (!candleRef.current || !volRef.current) return null
    try {
      const live = await get<LivePrice>(`/api/stocks/${ticker}/live`)
      if (!live.available || live.ltp == null) return live.market_status
      const close = live.ltp
      const open = live.prev_close ?? close
      const high = live.high ?? Math.max(open, close)
      const low = live.low ?? Math.min(open, close)
      const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Dhaka' }).format(new Date())
      candleRef.current.update({ time: today, open, high, low, close })
      volRef.current.update({
        time: today,
        value: Number(live.volume ?? 0),
        color: close >= open ? 'rgba(0,212,164,0.22)' : 'rgba(255,77,106,0.22)',
      })
      const vpBar = { time: today, open, high, low, close, volume: Number(live.volume ?? 0) }
      const arr = dataRef.current
      if (arr.length && arr[arr.length - 1].time === today) arr[arr.length - 1] = vpBar
      else arr.push(vpBar)
      if (vpOnRef.current) recomputeVP()
      return live.market_status
    } catch {
      return null  // chart still shows historical data if live fetch fails
    }
  }, [ticker, recomputeVP])

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

      // Cache raw candles for the volume profile (series can't return its data).
      dataRef.current = sorted.map(d => ({
        time:   d.day,
        open:   Number(d.open  ?? d.close),
        high:   Number(d.high  ?? d.close),
        low:    Number(d.low   ?? d.close),
        close:  Number(d.close),
        volume: Number(d.volume ?? 0),
      }))

      chartRef.current?.timeScale().fitContent()
      await appendLiveBar()  // overlay today's live candle on the historical data + recompute VP
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [ticker, appendLiveBar])

  // Module handle so overlay helpers can use lc.* (LineStyle, createSeriesMarkers)
  // outside the import().then closure.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const lcModRef = useRef<any>(null)

  // Init chart — recreate when ticker changes
  useEffect(() => {
    if (!containerRef.current) return

    let destroyed = false

    import('lightweight-charts').then(lc => {
      if (destroyed || !containerRef.current) return
      lcModRef.current = lc

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

      // OHLCV hover strip + Wyckoff event tooltip
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      chart.subscribeCrosshairMove((param: any) => {
        if (!param.time || !param.point) { setHovered(null); setEventTip(null); return }
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
        const tip = eventHelpRef.current[String(param.time)]
        setEventTip(tip ?? null)
      })

      chartRef.current  = chart
      candleRef.current = candleSeries
      volRef.current    = volSeries

      chart.timeScale().subscribeVisibleLogicalRangeChange(scheduleVP)

      loadData('1Y')
    })

    return () => {
      destroyed = true
      clearWyckoff()
      markersRef.current = null
      if (vpRafRef.current != null) { cancelAnimationFrame(vpRafRef.current); vpRafRef.current = null }
      try { chartRef.current?.timeScale().unsubscribeVisibleLogicalRangeChange(scheduleVP) } catch { /* gone */ }
      chartRef.current?.remove()
      chartRef.current  = null
      candleRef.current = null
      volRef.current    = null
      vpRef.current     = null
      lcModRef.current  = null
      setActiveRange('1Y')
      setHovered(null)
      setEventTip(null)
      setWyckoffOn(false)
      wyckoffOnRef.current = false
      setVpOn(false)
      vpOnRef.current = false
    }
  }, [ticker, loadData, clearWyckoff, scheduleVP])

  // Range button → reload data. Also refresh Wyckoff overlays for the new range
  // (clear first, then refetch if still on). This effect synchronizes the
  // external chart (lightweight-charts) + overlays with the active range, which
  // is the documented escape hatch for setState-in-effect here.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadData(activeRange)
    clearWyckoff()
    if (wyckoffOnRef.current) loadWyckoff(activeRange)
  }, [activeRange, loadData, clearWyckoff, loadWyckoff])

  // Toggle handler — flips the overlay on/off.
  const toggleWyckoff = useCallback(() => {
    setWyckoffOn(prev => {
      const next = !prev
      wyckoffOnRef.current = next
      if (next) {
        loadWyckoff(activeRange)
      } else {
        clearWyckoff()
      }
      return next
    })
  }, [activeRange, loadWyckoff, clearWyckoff])

  // Toggle the Volume Profile overlay on/off.
  const toggleVP = useCallback(() => {
    setVpOn(prev => {
      const next = !prev
      vpOnRef.current = next
      if (next) {
        if (candleRef.current && !vpRef.current) {
          vpRef.current = new VolumeProfilePrimitive()
          candleRef.current.attachPrimitive(vpRef.current)
        }
        recomputeVP()
      } else {
        if (candleRef.current && vpRef.current) {
          try { candleRef.current.detachPrimitive(vpRef.current) } catch { /* gone */ }
        }
        vpRef.current = null
        if (vpRafRef.current != null) { cancelAnimationFrame(vpRafRef.current); vpRafRef.current = null }
      }
      return next
    })
  }, [recomputeVP])

  // Refresh today's live candle every 2 min while the market is open
  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setInterval> | null = null
    async function tick() {
      const status = await appendLiveBar()
      if (!alive) return
      if (status && status !== 'Open' && timer) {
        clearInterval(timer)
        timer = null
      }
    }
    timer = setInterval(tick, 120_000)
    return () => {
      alive = false
      if (timer) clearInterval(timer)
    }
  }, [appendLiveBar])

  return (
    <div className="flex flex-col gap-3 h-full">
      {/* Controls row */}
      <div className="flex items-center justify-between min-h-[26px]">
        <div className="flex gap-1 items-center">
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
          {/* Wyckoff toggle — same styling language as the range buttons */}
          <button
            onClick={toggleWyckoff}
            title="Overlay Wyckoff Method analysis (trading ranges, phases, climaxes)"
            className={`ml-2 px-2.5 py-0.5 text-[11px] font-mono rounded transition-all flex items-center gap-1 ${
              wyckoffOn
                ? 'bg-[#a78bfa]/15 text-[#a78bfa] border border-[#a78bfa]/30'
                : 'text-[#6b6b80] hover:text-[#e8e8f0] border border-transparent hover:border-[#2a2a3a]'
            }`}
          >
            {wyckoffBusy && (
              <span className="w-1.5 h-1.5 rounded-full bg-[#a78bfa] animate-pulse" />
            )}
            Wyckoff
          </button>
          {/* Volume Profile toggle */}
          <button
            onClick={toggleVP}
            title="Overlay Volume Profile — volume traded per price level over the visible range (POC + 70% value area). Recomputes on pan/zoom."
            className={`px-2.5 py-0.5 text-[11px] font-mono rounded transition-all ${
              vpOn
                ? 'bg-[#7aa2f7]/15 text-[#7aa2f7] border border-[#7aa2f7]/30'
                : 'text-[#6b6b80] hover:text-[#e8e8f0] border border-transparent hover:border-[#2a2a3a]'
            }`}
          >
            VP
          </button>
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

      {/* Wyckoff legend chips — phase · confidence · dates, help on hover */}
      {wyckoffOn && wyckoffRanges.length > 0 && (
        <div className="flex flex-wrap gap-1.5 min-h-[20px]">
          {wyckoffRanges.map((rg, i) => {
            const color = PHASE_COLOR[rg.phase] ?? NEUTRAL
            const phaseLabel = rg.phase.charAt(0).toUpperCase() + rg.phase.slice(1)
            return (
              <span
                key={`${rg.start_day}-${i}`}
                title={rg.phase_help}
                className="px-2 py-0.5 text-[10px] font-mono rounded border cursor-help"
                style={{
                  color,
                  borderColor: `${color}55`,
                  backgroundColor: `${color}14`,
                }}
              >
                {phaseLabel} · {Math.round(rg.confidence * 100)}% ·{' '}
                {fmtShortDate(rg.start_day)}–{fmtShortDate(rg.end_day)}
              </span>
            )
          })}
        </div>
      )}

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
        {/* Wyckoff event tooltip — floating box near the top-left of the chart */}
        {eventTip && (
          <div className="absolute top-2 left-2 z-10 max-w-[280px] pointer-events-none rounded border border-[#2a2a3a] bg-[#161620]/95 px-2.5 py-1.5 shadow-lg">
            <div className="text-[11px] font-mono text-[#a78bfa] mb-0.5">{eventTip.label}</div>
            <div className="text-[10px] font-mono leading-snug text-[#b8b8c8]">{eventTip.help}</div>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Phase box primitive ─────────────────────────────────────────────────────
// lightweight-charts v5 has no native rectangle. This is a minimal
// ISeriesPrimitive that draws a translucent shaded box spanning the range's
// time width (start_day → end_day) and price height (support → resistance),
// plus a phase label in the top-left corner of the box.

interface PhaseBoxProps {
  startDay: string
  endDay: string
  support: number
  resistance: number
  color: string
  label: string
}

// hex (#rrggbb) → rgba string with the given alpha
function withAlpha(hex: string, alpha: number): string {
  const h = hex.replace('#', '')
  const r = parseInt(h.slice(0, 2), 16)
  const g = parseInt(h.slice(2, 4), 16)
  const b = parseInt(h.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${alpha})`
}

class PhaseBoxPaneRenderer {
  constructor(
    private p: PhaseBoxProps,
    private coords: () => { x1: number | null; x2: number | null; y1: number | null; y2: number | null } | null,
  ) {}

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  draw(target: any) {
    const get = this.coords()
    if (!get) return
    const { x1, x2, y1, y2 } = get
    if (x1 == null || x2 == null || y1 == null || y2 == null) return
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    target.useBitmapCoordinateSpace((scope: any) => {
      const ctx = scope.context as CanvasRenderingContext2D
      const hr = scope.horizontalPixelRatio
      const vr = scope.verticalPixelRatio
      const left = Math.min(x1, x2) * hr
      const right = Math.max(x1, x2) * hr
      const top = Math.min(y1, y2) * vr
      const bottom = Math.max(y1, y2) * vr
      const w = right - left
      const h = bottom - top

      ctx.fillStyle = withAlpha(this.p.color, 0.1)
      ctx.fillRect(left, top, w, h)
      ctx.strokeStyle = withAlpha(this.p.color, 0.5)
      ctx.lineWidth = 1 * Math.min(hr, vr)
      ctx.strokeRect(left, top, w, h)

      // Phase label, top-left inside the box
      const label = this.p.label.charAt(0).toUpperCase() + this.p.label.slice(1)
      ctx.font = `${10 * vr}px Menlo, Consolas, monospace`
      ctx.fillStyle = withAlpha(this.p.color, 0.95)
      ctx.textBaseline = 'top'
      ctx.fillText(label, left + 4 * hr, top + 3 * vr)
    })
  }
}

class PhaseBoxPaneView {
  private _renderer: PhaseBoxPaneRenderer
  constructor(
    p: PhaseBoxProps,
    coords: () => { x1: number | null; x2: number | null; y1: number | null; y2: number | null } | null,
  ) {
    this._renderer = new PhaseBoxPaneRenderer(p, coords)
  }
  zOrder() {
    return 'bottom' as const
  }
  renderer() {
    return this._renderer
  }
}

class PhaseBoxPrimitive {
  private p: PhaseBoxProps
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private chart: any = null
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private series: any = null
  private requestUpdate: (() => void) | null = null
  private views: PhaseBoxPaneView[]

  constructor(p: PhaseBoxProps) {
    this.p = p
    this.views = [new PhaseBoxPaneView(p, () => this.computeCoords())]
  }

  private computeCoords() {
    if (!this.chart || !this.series) return null
    const ts = this.chart.timeScale()
    const x1 = ts.timeToCoordinate(this.p.startDay)
    const x2 = ts.timeToCoordinate(this.p.endDay)
    const y1 = this.series.priceToCoordinate(this.p.resistance)
    const y2 = this.series.priceToCoordinate(this.p.support)
    return { x1, x2, y1, y2 }
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  attached(param: any) {
    this.chart = param.chart
    this.series = param.series
    this.requestUpdate = param.requestUpdate
  }

  detached() {
    this.chart = null
    this.series = null
    this.requestUpdate = null
  }

  updateAllViews() {
    // coordinates are computed lazily in draw(); nothing to cache here
  }

  paneViews() {
    return this.views
  }
}

// ─── Volume Profile primitive ────────────────────────────────────────────────
// Draws a left-anchored horizontal histogram of volume-by-price for the current
// VProfile, behind the candles (zOrder 'bottom'). POC drawn as a bright amber
// line; value-area bins shaded slightly stronger than the rest.

interface VPDrawState {
  profile: VProfile | null
  priceToCoordinate: (price: number) => number | null
}

class VolumeProfilePaneRenderer {
  constructor(private state: () => VPDrawState | null) {}

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  draw(target: any) {
    const s = this.state()
    if (!s || !s.profile || s.profile.maxVol <= 0) return
    const { profile, priceToCoordinate } = s
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    target.useBitmapCoordinateSpace((scope: any) => {
      const ctx = scope.context as CanvasRenderingContext2D
      const hr = scope.horizontalPixelRatio
      const vr = scope.verticalPixelRatio
      const maxBarPx = scope.mediaSize.width * VP_BAR_MAX_FRACTION

      for (const bin of profile.bins) {
        const yHigh = priceToCoordinate(bin.priceHigh)
        const yLow = priceToCoordinate(bin.priceLow)
        if (yHigh == null || yLow == null) continue
        const top = Math.min(yHigh, yLow) * vr
        const bottom = Math.max(yHigh, yLow) * vr
        const h = Math.max(1 * vr, bottom - top - 1 * vr) // 1px gap between bars
        const w = (bin.volume / profile.maxVol) * maxBarPx * hr
        const inVA = profile.vah > profile.val
          ? bin.priceHigh > profile.val && bin.priceLow < profile.vah
          : true // degenerate (single-bin) profile: the sole bin IS the value area
        ctx.fillStyle = withAlpha(VP_COLOR, inVA ? VP_VA_ALPHA : VP_BAR_ALPHA)
        ctx.fillRect(0, top, w, h)
      }

      // POC line across the full bar width
      const yPoc = priceToCoordinate(profile.poc)
      if (yPoc != null) {
        const y = yPoc * vr
        ctx.fillStyle = VP_POC_COLOR
        ctx.fillRect(0, y - 0.5 * vr, maxBarPx * hr, Math.max(1, 1 * vr))
      }
    })
  }
}

class VolumeProfilePaneView {
  private _renderer: VolumeProfilePaneRenderer
  constructor(state: () => VPDrawState | null) {
    this._renderer = new VolumeProfilePaneRenderer(state)
  }
  zOrder() {
    return 'bottom' as const
  }
  renderer() {
    return this._renderer
  }
}

class VolumeProfilePrimitive {
  private profile: VProfile | null = null
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private series: any = null
  private requestUpdate: (() => void) | null = null
  private views: VolumeProfilePaneView[]

  constructor() {
    this.views = [new VolumeProfilePaneView(() => this.drawState())]
  }

  private drawState(): VPDrawState | null {
    if (!this.series) return null
    return {
      profile: this.profile,
      priceToCoordinate: (price: number) => this.series.priceToCoordinate(price),
    }
  }

  setProfile(profile: VProfile | null) {
    this.profile = profile
    this.requestUpdate?.()
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  attached(param: any) {
    this.series = param.series
    this.requestUpdate = param.requestUpdate
  }

  detached() {
    this.series = null
    this.requestUpdate = null
  }

  updateAllViews() {
    // profile is read lazily in draw(); nothing to cache
  }

  paneViews() {
    return this.views
  }
}
