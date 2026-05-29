type Rating = 'STRONG BUY' | 'BUY' | 'HOLD' | 'SELL' | 'STRONG SELL' | 'N/A'

function getRating(score: number | null): Rating {
  if (score == null) return 'N/A'
  if (score >= 80) return 'STRONG BUY'
  if (score >= 60) return 'BUY'
  if (score >= 40) return 'HOLD'
  if (score >= 20) return 'SELL'
  return 'STRONG SELL'
}

export default function RatingBadge({ score }: { score: number | null }) {
  const rating = getRating(score)

  const colorMap: Record<string, string> = {
    'STRONG BUY':  'bg-[#00d4a4]/15 text-[#00d4a4] border-[#00d4a4]/40',
    'BUY':         'bg-[#00d4a4]/8  text-[#00cfaa] border-[#00d4a4]/25',
    'HOLD':        'bg-[#f5c842]/15 text-[#f5c842] border-[#f5c842]/40',
    'SELL':        'bg-[#ff4d6a]/8  text-[#ff6680] border-[#ff4d6a]/25',
    'STRONG SELL': 'bg-[#ff4d6a]/15 text-[#ff4d6a] border-[#ff4d6a]/40',
    'N/A':         'bg-[#6b6b80]/10 text-[#6b6b80] border-[#6b6b80]/20',
  }

  return (
    <span className={`inline-flex items-center px-3 py-1 rounded-md border text-[11px] font-mono font-semibold tracking-wider uppercase ${colorMap[rating]}`}>
      {rating}
    </span>
  )
}
