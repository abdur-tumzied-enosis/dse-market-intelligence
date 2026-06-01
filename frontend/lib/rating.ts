// frontend/lib/rating.ts
export type Rating =
  | 'STRONG_BUY' | 'BUY' | 'HOLD' | 'SELL' | 'STRONG_SELL' | 'N/A'

export function getRating(score: number | null): Rating {
  if (score == null) return 'N/A'
  if (score >= 80) return 'STRONG_BUY'
  if (score >= 60) return 'BUY'
  if (score >= 40) return 'HOLD'
  if (score >= 20) return 'SELL'
  return 'STRONG_SELL'
}
