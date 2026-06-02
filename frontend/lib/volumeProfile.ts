// frontend/lib/volumeProfile.ts
// Pure volume-profile math: bin candle volume by price level, derive the
// Point of Control (POC) and the 70% Value Area band (VAH/VAL). No chart or
// React dependency so it is unit-testable in isolation.

export interface VPCandle {
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface VPBin {
  priceLow: number
  priceHigh: number
  volume: number
}

export interface VProfile {
  bins: VPBin[]   // ascending by price
  poc: number     // mid-price of the max-volume bin
  vah: number     // value-area high (top edge of the 70% band)
  val: number     // value-area low  (bottom edge of the 70% band)
  maxVol: number  // largest single-bin volume (for bar-width scaling)
}

const VALUE_AREA_FRACTION = 0.7

export function computeVolumeProfile(
  candles: VPCandle[],
  binCount = 24,
): VProfile | null {
  const valid = candles.filter(
    c =>
      Number.isFinite(c.high) &&
      Number.isFinite(c.low) &&
      Number.isFinite(c.volume) &&
      c.volume > 0,
  )
  if (valid.length === 0) return null

  let lo = Infinity
  let hi = -Infinity
  let totalVol = 0
  for (const c of valid) {
    if (c.low < lo) lo = c.low
    if (c.high > hi) hi = c.high
    totalVol += c.volume
  }
  if (totalVol <= 0) return null

  // Single candle: volume-at-price is unknowable from one daily bar, so
  // collapse to one bin at its close. Flat price (zero extent): collapse to lo.
  if (valid.length === 1) {
    const p = valid[0].close
    return { bins: [{ priceLow: p, priceHigh: p, volume: totalVol }], poc: p, vah: p, val: p, maxVol: totalVol }
  }
  if (hi <= lo) {
    return { bins: [{ priceLow: lo, priceHigh: lo, volume: totalVol }], poc: lo, vah: lo, val: lo, maxVol: totalVol }
  }

  const n = Math.max(1, Math.floor(binCount))
  const step = (hi - lo) / n
  const bins: VPBin[] = Array.from({ length: n }, (_, i) => ({
    priceLow: lo + i * step,
    priceHigh: lo + (i + 1) * step,
    volume: 0,
  }))

  // Spread each candle's volume evenly across every bin its [low, high] spans.
  for (const c of valid) {
    const lowIdx = Math.min(n - 1, Math.max(0, Math.floor((c.low - lo) / step)))
    const highIdx = Math.min(n - 1, Math.max(0, Math.floor((c.high - lo) / step)))
    const span = highIdx - lowIdx + 1
    const share = c.volume / span
    for (let i = lowIdx; i <= highIdx; i++) bins[i].volume += share
  }

  // POC = bin with the most volume.
  let pocIdx = 0
  let maxVol = 0
  for (let i = 0; i < n; i++) {
    if (bins[i].volume > maxVol) {
      maxVol = bins[i].volume
      pocIdx = i
    }
  }
  const poc = (bins[pocIdx].priceLow + bins[pocIdx].priceHigh) / 2

  // Value area: grow outward from the POC bin, each step adding the neighbour
  // (above or below) with the larger volume, until >= 70% of total volume.
  let acc = bins[pocIdx].volume
  let loIdx = pocIdx
  let hiIdx = pocIdx
  const target = totalVol * VALUE_AREA_FRACTION
  while (acc < target && (loIdx > 0 || hiIdx < n - 1)) {
    const below = loIdx > 0 ? bins[loIdx - 1].volume : -1
    const above = hiIdx < n - 1 ? bins[hiIdx + 1].volume : -1
    if (above >= below) {
      hiIdx += 1
      acc += bins[hiIdx].volume
    } else {
      loIdx -= 1
      acc += bins[loIdx].volume
    }
  }

  return { bins, poc, vah: bins[hiIdx].priceHigh, val: bins[loIdx].priceLow, maxVol }
}
