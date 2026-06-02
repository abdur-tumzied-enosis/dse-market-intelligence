import { computeVolumeProfile } from '@/lib/volumeProfile'
import type { VPCandle } from '@/lib/volumeProfile'

const c = (low: number, high: number, volume: number, close = (low + high) / 2): VPCandle => ({
  open: close,
  high,
  low,
  close,
  volume,
})

describe('computeVolumeProfile', () => {
  it('bins volume by price, finds POC and a 70% value area', () => {
    // Two non-overlapping candles, 2 bins over [0,10] (step 5):
    //   A: low 0 high 4 vol 100 -> bin0 (100)
    //   B: low 6 high 10 vol 60 -> bin1 (60)
    const profile = computeVolumeProfile([c(0, 4, 100), c(6, 10, 60)], 2)!
    expect(profile).not.toBeNull()
    expect(profile.bins).toHaveLength(2)
    expect(profile.bins[0].volume).toBeCloseTo(100, 5)
    expect(profile.bins[1].volume).toBeCloseTo(60, 5)
    expect(profile.maxVol).toBeCloseTo(100, 5)
    expect(profile.poc).toBeCloseTo(2.5, 5) // mid of bin0 [0,5)
    // value area expands from POC until >=70% of 160 (=112): needs both bins
    expect(profile.val).toBeCloseTo(0, 5)
    expect(profile.vah).toBeCloseTo(10, 5)
  })

  it('spreads a candle volume evenly across every bin it spans', () => {
    // Single-candle case is special (collapses); use two identical wide candles.
    // Range [0,10], 2 bins. Each candle spans both bins -> half each.
    const profile = computeVolumeProfile([c(0, 10, 80), c(0, 10, 80)], 2)!
    expect(profile.bins[0].volume).toBeCloseTo(80, 5)
    expect(profile.bins[1].volume).toBeCloseTo(80, 5)
  })

  it('returns null for empty input', () => {
    expect(computeVolumeProfile([], 24)).toBeNull()
  })

  it('returns null when total volume is zero', () => {
    expect(computeVolumeProfile([c(1, 2, 0), c(3, 4, 0)], 24)).toBeNull()
  })

  it('collapses a single candle to one bin at its close', () => {
    const profile = computeVolumeProfile([c(10, 20, 50, 15)], 24)!
    expect(profile.bins).toHaveLength(1)
    expect(profile.bins[0].volume).toBeCloseTo(50, 5)
    expect(profile.poc).toBeCloseTo(15, 5)
    expect(profile.vah).toBeCloseTo(15, 5)
    expect(profile.val).toBeCloseTo(15, 5)
    expect(profile.maxVol).toBeCloseTo(50, 5)
  })

  it('handles flat price (zero extent) without NaN', () => {
    const profile = computeVolumeProfile([c(10, 10, 30), c(10, 10, 20)], 24)!
    expect(profile.bins).toHaveLength(1)
    expect(profile.bins[0].volume).toBeCloseTo(50, 5)
    expect(profile.poc).toBeCloseTo(10, 5)
    expect(profile.vah).toBeCloseTo(10, 5)
    expect(profile.val).toBeCloseTo(10, 5)
    expect(Number.isNaN(profile.poc)).toBe(false)
  })
})
