import type { MarketRegime } from '@/lib/types'

const EXPLAINER =
  'Bull vs Bear trend gauge. DSEX is the Dhaka Stock Exchange broad index — one number for the whole market. MA is its 50-day moving average (the recent trend line). When DSEX sits above the average the market is trending up (Bull); below it, trending down (Bear). The % is how far above or below the trend it is. "Provisional" means fewer than 50 trading days of history so far, so the average is still settling.'

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
      <div className="flex items-center gap-1.5">
        <p className="text-xs text-muted uppercase tracking-widest">Market Regime</p>
        <span className="group/help relative inline-flex">
          <span
            tabIndex={0}
            title={EXPLAINER}
            aria-label={EXPLAINER}
            className="flex h-3.5 w-3.5 cursor-help items-center justify-center rounded-full border border-border-custom text-[9px] font-bold text-muted hover:text-white hover:border-muted focus:outline-none"
          >
            ?
          </span>
          <span
            role="tooltip"
            className="pointer-events-none absolute left-0 top-full z-20 mt-1.5 w-64 rounded-md border border-border-custom bg-surface px-3 py-2 text-xs leading-relaxed text-gray-300 opacity-0 shadow-lg transition-opacity duration-150 group-hover/help:opacity-100 group-focus-within/help:opacity-100"
          >
            {EXPLAINER}
          </span>
        </span>
      </div>
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
