import Link from 'next/link'
import type { StockListItem } from '@/lib/types'

function fmtCap(bdt: number | null): string {
  if (!bdt) return '—'
  const cr = bdt / 10_000_000
  if (cr >= 1000) return `৳${(cr / 1000).toFixed(1)}K Cr`
  return `৳${cr.toFixed(0)} Cr`
}

interface Props {
  stock: StockListItem
  rank: number
}

export default function StockCard({ stock, rank }: Props) {
  return (
    <tr className="border-b border-[#1a1a24] hover:bg-[#111118] transition-colors group">
      <td className="py-3 pl-4 pr-2 text-[#6b6b80] text-[11px] font-mono w-10">{rank}</td>
      <td className="py-3 px-2">
        <Link href={`/stocks/${stock.ticker}`}
          className="font-mono font-bold text-sm text-[#4d9eff] hover:text-[#7ab8ff] transition-colors tracking-wide">
          {stock.ticker}
        </Link>
      </td>
      <td className="py-3 px-2">
        <Link href={`/stocks/${stock.ticker}`}
          className="text-sm text-[#e8e8f0] group-hover:text-white transition-colors line-clamp-1 max-w-[240px] block">
          {stock.name}
        </Link>
      </td>
      <td className="py-3 px-2 hidden md:table-cell">
        <span className="text-[11px] font-mono text-[#6b6b80] bg-[#1a1a24] px-2 py-0.5 rounded border border-[#2a2a3a]">
          {stock.sector}
        </span>
      </td>
      <td className="py-3 px-2 hidden sm:table-cell">
        <span className="text-[11px] font-mono text-[#6b6b80]">
          {stock.category ?? '—'}
        </span>
      </td>
      <td className="py-3 px-2 text-right">
        <span className="text-sm font-mono tabular-nums text-[#e8e8f0]">
          {fmtCap(stock.market_cap_bdt)}
        </span>
      </td>
      <td className="py-3 pl-2 pr-4 text-right">
        <Link href={`/stocks/${stock.ticker}`}
          className="text-[11px] font-mono text-[#4d9eff] hover:text-[#7ab8ff] opacity-0 group-hover:opacity-100 transition-opacity">
          View →
        </Link>
      </td>
    </tr>
  )
}
