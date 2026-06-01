import { squarify } from '@/lib/treemap'

describe('squarify', () => {
  it('tiles fill the rect area (sum of areas ≈ rect area)', () => {
    const rect = { x: 0, y: 0, w: 100, h: 100 }
    const rects = squarify([4, 3, 2, 1], rect)
    expect(rects).toHaveLength(4)
    const area = rects.reduce((s, r) => s + r.w * r.h, 0)
    expect(area).toBeCloseTo(100 * 100, 0)
  })
  it('returns the full rect for every item when total is 0', () => {
    const rect = { x: 0, y: 0, w: 50, h: 50 }
    expect(squarify([0, 0], rect)).toEqual([rect, rect])
  })
})
