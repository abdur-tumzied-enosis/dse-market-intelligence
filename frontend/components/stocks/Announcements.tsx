import type { AnnouncementRow } from '@/lib/types'

// Map a free-form announcement_type to an accent colour + short label.
function typeStyle(type: string | null): { color: string; tag: string } {
  const t = (type ?? '').toLowerCase()
  if (t.includes('div')) return { color: '#f5c842', tag: 'Dividend' }
  if (t.includes('agm') || t.includes('egm') || t.includes('meeting'))
    return { color: '#4d9eff', tag: 'Meeting' }
  if (t.includes('eps') || t.includes('financial') || t.includes('earning') || t.includes('quarter'))
    return { color: '#00d4a4', tag: 'Earnings' }
  if (t.includes('price') || t.includes('sensitive'))
    return { color: '#ff4d6a', tag: 'PSI' }
  return { color: '#a78bfa', tag: type ? type.slice(0, 14) : 'Notice' }
}

function fmtDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

export default function Announcements({ items }: { items: AnnouncementRow[] }) {
  if (!items.length) {
    return (
      <p className="text-[11px] font-mono text-[#6b6b80]">No recent announcements.</p>
    )
  }

  return (
    <ol className="relative space-y-0">
      {/* vertical spine */}
      <span className="absolute left-[5px] top-1 bottom-1 w-px bg-[#2a2a3a]" aria-hidden />
      {items.map(a => {
        const { color, tag } = typeStyle(a.announcement_type)
        const chips: { label: string; color: string }[] = []
        if (a.dividend_cash_pct != null)
          chips.push({ label: `${Number(a.dividend_cash_pct)}% cash`, color: '#f5c842' })
        if (a.dividend_stock_pct != null)
          chips.push({ label: `${Number(a.dividend_stock_pct)}% stock`, color: '#4d9eff' })
        if (a.eps_value != null)
          chips.push({
            label: `EPS ৳${Number(a.eps_value).toFixed(2)}${a.eps_period ? ` · ${a.eps_period}` : ''}`,
            color: '#00d4a4',
          })

        return (
          <li key={a.id} className="relative pl-6 py-2.5 group">
            {/* node */}
            <span
              className="absolute left-0 top-3.5 h-[11px] w-[11px] rounded-full border-2 border-[#111118]"
              style={{ background: color, boxShadow: `0 0 6px ${color}66` }}
              aria-hidden
            />
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span
                    className="text-[9px] font-mono uppercase tracking-[0.14em] px-1.5 py-0.5 rounded"
                    style={{ color, backgroundColor: `${color}14`, border: `1px solid ${color}33` }}
                  >
                    {tag}
                  </span>
                  <span className="text-[10px] font-mono text-[#6b6b80] tabular-nums">
                    {fmtDate(a.published_at)}
                  </span>
                </div>
                <p className="text-[12px] text-[#d8d8e4] leading-snug group-hover:text-white transition-colors">
                  {a.headline}
                </p>
                {chips.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-1.5">
                    {chips.map(c => (
                      <span
                        key={c.label}
                        className="text-[10px] font-mono tabular-nums px-1.5 py-0.5 rounded"
                        style={{ color: c.color, backgroundColor: `${c.color}12` }}
                      >
                        {c.label}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
