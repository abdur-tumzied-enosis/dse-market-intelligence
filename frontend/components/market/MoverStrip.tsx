import type { TopMover } from '@/lib/types'

interface MoverStripProps {
  title: string
  movers: TopMover[]
  variant: 'gain' | 'loss'
}

export default function MoverStrip({ title, movers, variant }: MoverStripProps) {
  const changeColor = variant === 'gain' ? 'text-accent-green' : 'text-accent-red'
  return (
    <div className="bg-surface border border-border-custom rounded-lg p-4">
      <h3 className="text-xs text-muted uppercase tracking-widest mb-3">{title}</h3>
      <ul className="space-y-2">
        {movers.map((m) => (
          <li key={m.ticker} className="flex justify-between items-center">
            <div>
              <span className="text-sm font-medium text-white">{m.ticker}</span>
              <span className="text-xs text-muted ml-2 truncate max-w-[120px]">{m.name}</span>
            </div>
            <div className="text-right">
              <span className="text-sm text-white">{m.close.toFixed(1)}</span>
              <span className={`text-xs ml-2 ${changeColor}`}>
                {variant === 'gain' ? '+' : ''}{m.change_pct.toFixed(2)}%
              </span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
