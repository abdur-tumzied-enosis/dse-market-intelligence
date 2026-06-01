import MarketStreamBar from './MarketStreamBar'
import SearchTrigger from './SearchTrigger'

export default function TopBar() {
  return (
    <header className="h-14 border-b border-border bg-surface flex items-center px-6 gap-6">
      <span className="text-sm font-semibold text-white shrink-0">DSE Stock Intelligence</span>
      <SearchTrigger />
      <div className="flex-1" />
      <MarketStreamBar />
    </header>
  )
}
