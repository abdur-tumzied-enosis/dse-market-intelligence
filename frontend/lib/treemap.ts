// frontend/lib/treemap.ts
export interface Rect { x: number; y: number; w: number; h: number }

function worstAspect(row: number[], rowLen: number): number {
  const s = row.reduce((a, b) => a + b, 0)
  const rowW = s / rowLen
  let worst = 0
  for (const a of row) {
    const h = a / rowW
    worst = Math.max(worst, Math.max(rowW / h, h / rowW))
  }
  return worst
}

export function squarify(values: number[], rect: Rect): Rect[] {
  const total = values.reduce((a, b) => a + b, 0)
  if (total === 0 || rect.w <= 0 || rect.h <= 0) return values.map(() => rect)
  const area = rect.w * rect.h
  const scaled = values.map(v => (v / total) * area)
  const out: Rect[] = new Array(values.length)
  layout(scaled, rect, 0, out)
  return out
}

function layout(areas: number[], rect: Rect, offset: number, out: Rect[]) {
  if (areas.length === 0) return
  if (areas.length === 1) { out[offset] = rect; return }

  const { x, y, w, h } = rect
  const horizontal = w >= h
  const side = horizontal ? h : w

  // Build row greedily while aspect improves
  let row: number[] = []
  let i = 0
  for (; i < areas.length; i++) {
    const candidate = [...row, areas[i]]
    if (row.length > 0 && worstAspect(candidate, side) > worstAspect(row, side)) break
    row = candidate
  }

  const rowSum = row.reduce((a, b) => a + b, 0)
  const rowDim = rowSum / side
  let pos = horizontal ? y : x
  for (let j = 0; j < row.length; j++) {
    const itemDim = row[j] / rowDim
    out[offset + j] = horizontal
      ? { x, y: pos, w: rowDim, h: itemDim }
      : { x: pos, y, w: itemDim, h: rowDim }
    pos += itemDim
  }

  const nextRect = horizontal
    ? { x: x + rowDim, y, w: w - rowDim, h }
    : { x, y: y + rowDim, w, h: h - rowDim }
  layout(areas.slice(i), nextRect, offset + i, out)
}
