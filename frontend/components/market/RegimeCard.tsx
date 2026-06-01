import type { MarketRegime } from '@/lib/types'

export default function RegimeCard({ data }: { data: MarketRegime }) {
  const insufficient = data.data_status === 'insufficient'
  const isBull = data.regime === 'Bull'
  const accent = insufficient
    ? 'text-muted'
    : isBull
      ? 'text-accent-green'
      : 'text-accent-red'

  return (
    <div className="bg-surface border border-border-custom rounded-lg px-5 py-4 min-w-[148px]">
      <p className="text-xs text-muted uppercase tracking-widest">Market Regime</p>
      <p className={`text-xl font-semibold mt-1 ${accent}`}>
        {insufficient ? 'Collecting data' : data.regime}
      </p>
      {!insufficient && data.dsex != null && data.ma != null && (
        <p className="text-sm text-muted mt-0.5">
          DSEX {data.dsex.toFixed(2)} vs MA {data.ma.toFixed(2)}
          {data.distance_pct != null && (
            <span className={`ml-1 ${accent}`}>
              ({data.distance_pct >= 0 ? '+' : ''}{data.distance_pct.toFixed(2)}%)
            </span>
          )}
        </p>
      )}
      {data.provisional && !insufficient && (
        <p className="text-xs text-muted mt-0.5">provisional ({data.window}/50d)</p>
      )}
      {insufficient && (
        <p className="text-xs text-muted mt-0.5">{data.window}/50d collected</p>
      )}
    </div>
  )
}
