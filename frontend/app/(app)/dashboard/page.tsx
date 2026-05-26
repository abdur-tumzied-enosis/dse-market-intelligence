import { serverApi } from '@/lib/server-api'
import IndexCard from '@/components/market/IndexCard'
import MoverStrip from '@/components/market/MoverStrip'
import HeatmapGrid from '@/components/market/HeatmapGrid'

export default async function DashboardPage() {
  const [indices, movers, heatmap] = await Promise.allSettled([
    serverApi.market.indices(),
    serverApi.market.movers(),
    serverApi.market.heatmap(),
  ])

  const idx = indices.status === 'fulfilled' ? indices.value : null
  const mv  = movers.status === 'fulfilled'  ? movers.value  : null
  const hm  = heatmap.status === 'fulfilled' ? heatmap.value : []

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-white">Market Overview</h1>
        {idx && (
          <span className={`text-xs px-2 py-1 rounded font-medium ${
            idx.market_status === 'Open' ? 'bg-accent-green/20 text-accent-green' : 'bg-muted/20 text-muted'
          }`}>
            {idx.market_status}
          </span>
        )}
      </div>

      {idx ? (
        <div className="flex flex-wrap gap-4 mb-6">
          <IndexCard label="DSEX" value={idx.dsex_value} changePct={idx.dsex_change_pct} />
          <IndexCard label="DS30" value={idx.ds30_value} changePct={idx.ds30_change_pct} />
          <IndexCard label="DSES" value={idx.dses_value} changePct={idx.dses_change_pct} />
          <div className="bg-surface border border-border-custom rounded-lg px-5 py-4 min-w-[148px]">
            <p className="text-xs text-muted uppercase tracking-widest">Advance / Decline</p>
            <p className="text-xl font-semibold text-white mt-1">
              <span className="text-accent-green">{idx.advance}</span>
              <span className="text-muted mx-1">/</span>
              <span className="text-accent-red">{idx.decline}</span>
            </p>
            <p className="text-xs text-muted mt-0.5">{idx.unchanged} unchanged</p>
          </div>
        </div>
      ) : (
        <p className="text-muted text-sm mb-6">Market data unavailable.</p>
      )}

      {mv && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
          <MoverStrip title="Top Gainers" movers={mv.gainers} variant="gain" />
          <MoverStrip title="Top Losers" movers={mv.losers} variant="loss" />
        </div>
      )}

      <HeatmapGrid items={hm} />
    </div>
  )
}
