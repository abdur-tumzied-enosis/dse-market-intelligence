interface IndexCardProps {
  label: string
  value: number
  changePct: number
}

export default function IndexCard({ label, value, changePct }: IndexCardProps) {
  const v = Number(value)
  const pct = Number(changePct)
  const isUp = pct >= 0
  const sign = isUp ? '+' : ''
  return (
    <div className="bg-surface border border-border-custom rounded-lg px-5 py-4 min-w-[148px]">
      <p className="text-xs text-muted uppercase tracking-widest">{label}</p>
      <p className="text-xl font-semibold text-white mt-1">
        {v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </p>
      <p className={`text-sm font-medium mt-0.5 ${isUp ? 'text-accent-green' : 'text-accent-red'}`}>
        {sign}{pct.toFixed(2)}%
      </p>
    </div>
  )
}
