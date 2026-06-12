import type { CompanyInfo, LatestFundamentals } from '@/lib/types'

function fmtDate(iso: string | null | undefined): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

function loanCr(mn: number | null | undefined): number | null {
  if (mn == null) return null
  return Number(mn) / 10 // DSE prints loans in millions; 10 mn = 1 crore
}

function fmtCr(cr: number): string {
  if (cr === 0) return '৳0'
  return `৳${cr >= 100 ? Math.round(cr).toLocaleString('en-US') : cr.toFixed(1)} Cr`
}

/** Heuristic traffic-light for BD credit-rating scales (CRISL/CRAB style). */
function ratingColor(r: string): string {
  const s = r.toUpperCase().trim()
  if (/^ST-[12]\b/.test(s) || /^AA/.test(s)) return '#00d4a4'
  if (/^ST-[34]\b/.test(s) || /^A/.test(s) || /^BBB/.test(s)) return '#f5c842'
  return '#ff4d6a'
}

function Chip({ label, text, color }: { label: string; text: string; color: string }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[10px] font-mono"
      style={{ color, backgroundColor: `${color}12`, border: `1px solid ${color}30` }}
    >
      <span className="uppercase tracking-wider opacity-60">{label}</span>
      <span className="font-semibold">{text}</span>
    </span>
  )
}

interface Props {
  company: CompanyInfo
  latest: LatestFundamentals | null
  /** Render the sponsor/public fallback bar (no dated shareholding history exists). */
  showOwnership: boolean
}

export default function CompanyProfile({ company, latest, showOwnership }: Props) {
  const facts: { label: string; value: string }[] = [
    { label: 'Sector', value: company.sector },
    { label: 'Category', value: company.category ?? '' },
    { label: 'Listed', value: fmtDate(company.listing_date) ?? '' },
    { label: 'Debut Trading', value: fmtDate(company.debut_trading_date) ?? '' },
    { label: 'Fiscal Year', value: latest?.fiscal_year != null ? String(latest.fiscal_year) : '' },
    { label: 'Face Value', value: company.face_value != null ? `৳${Number(company.face_value).toFixed(0)}` : '' },
    { label: 'Market Lot', value: company.market_lot != null ? String(company.market_lot) : '' },
    { label: 'ISIN', value: company.isin ?? '' },
  ].filter(f => f.value !== '')

  const shortCr = loanCr(company.short_loan_mn)
  const longCr = loanCr(company.long_loan_mn)
  const totalCr = shortCr != null || longCr != null ? (shortCr ?? 0) + (longCr ?? 0) : null
  const loanDate = fmtDate(company.loan_as_on)

  const hasRatings =
    company.credit_rating_st != null ||
    company.credit_rating_lt != null ||
    company.operational_status != null
  const hasRisk = totalCr != null || hasRatings

  // Ownership fallback split (latest fundamentals row only)
  const sponsor = latest?.sponsor_pct != null ? Number(latest.sponsor_pct) : null
  const publicPct = latest?.public_pct != null ? Number(latest.public_pct) : null
  const ownTotal = (sponsor ?? 0) + (publicPct ?? 0)

  return (
    <div className="px-4 pb-4 space-y-4">
      {/* Fact tiles */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
        {facts.map(({ label, value }) => (
          <div key={label} className="rounded-lg border border-[#23232f] bg-[#111118] px-3 py-2">
            <p className="text-[9px] font-mono uppercase tracking-[0.15em] text-[#56566a] mb-0.5">
              {label}
            </p>
            <p className="text-[12px] font-mono text-[#d8d8e4] truncate" title={value}>
              {value}
            </p>
          </div>
        ))}
      </div>

      {/* Risk & leverage (DSE company-page loans/ratings/status) */}
      {hasRisk && (
        <div className="border-t border-[#1f1f2c] pt-3">
          <p className="text-[9px] font-mono uppercase tracking-[0.2em] text-[#56566a] mb-2.5">
            Risk &amp; Leverage
          </p>
          <div className="grid gap-x-8 gap-y-3 md:grid-cols-2">
            {totalCr != null && (
              <div>
                <div className="flex items-baseline justify-between mb-1.5">
                  <span className="text-[10px] font-mono text-[#8a8a9e]">
                    Total Borrowings{loanDate && <span className="text-[#56566a]"> · as on {loanDate}</span>}
                  </span>
                  <span className="text-[15px] font-mono font-semibold text-[#e8e8f0] tabular-nums">
                    {fmtCr(totalCr)}
                  </span>
                </div>
                {totalCr > 0 ? (
                  <>
                    <div className="flex h-2 rounded-full overflow-hidden bg-[#1a1a24]">
                      {(shortCr ?? 0) > 0 && (
                        <div className="h-full" style={{ width: `${((shortCr ?? 0) / totalCr) * 100}%`, background: '#f5c842' }} />
                      )}
                      {(longCr ?? 0) > 0 && (
                        <div className="h-full" style={{ width: `${((longCr ?? 0) / totalCr) * 100}%`, background: '#4d9eff' }} />
                      )}
                    </div>
                    <div className="flex justify-between mt-1.5 text-[10px] font-mono">
                      <span className="text-[#f5c842]">
                        Short-term <span className="text-[#d8d8e4] tabular-nums">{shortCr != null ? fmtCr(shortCr) : '—'}</span>
                      </span>
                      <span className="text-[#4d9eff]">
                        Long-term <span className="text-[#d8d8e4] tabular-nums">{longCr != null ? fmtCr(longCr) : '—'}</span>
                      </span>
                    </div>
                  </>
                ) : (
                  <p className="text-[10px] font-mono text-[#00d4a4]">Debt-free as reported</p>
                )}
              </div>
            )}
            {hasRatings && (
              <div className="flex flex-wrap content-start gap-1.5">
                {company.operational_status && (
                  <Chip
                    label="Status"
                    text={company.operational_status}
                    color={/operat/i.test(company.operational_status) ? '#00d4a4' : '#f5c842'}
                  />
                )}
                {company.credit_rating_st && (
                  <Chip label="Credit ST" text={company.credit_rating_st} color={ratingColor(company.credit_rating_st)} />
                )}
                {company.credit_rating_lt && (
                  <Chip label="Credit LT" text={company.credit_rating_lt} color={ratingColor(company.credit_rating_lt)} />
                )}
              </div>
            )}
          </div>
          {company.delisting_remark && (
            <p className="mt-3 rounded-md border border-[#ff4d6a]/30 bg-[#ff4d6a]/10 px-2.5 py-1.5 text-[10px] font-mono text-[#ff4d6a]">
              {company.delisting_remark}
            </p>
          )}
        </div>
      )}

      {/* Ownership fallback — only when no dated shareholding history exists */}
      {showOwnership && ownTotal > 0 && (
        <div className="border-t border-[#1f1f2c] pt-3">
          <p className="text-[9px] font-mono uppercase tracking-[0.2em] text-[#56566a] mb-2">
            Ownership
          </p>
          <div className="flex h-2.5 rounded-full overflow-hidden bg-[#1a1a24]">
            {sponsor != null && (
              <div className="h-full" style={{ width: `${(sponsor / ownTotal) * 100}%`, background: '#a78bfa' }} />
            )}
            {publicPct != null && (
              <div className="h-full" style={{ width: `${(publicPct / ownTotal) * 100}%`, background: '#4d9eff' }} />
            )}
          </div>
          <div className="flex justify-between mt-2 text-[10px] font-mono">
            <span className="text-[#a78bfa]">
              Sponsor <span className="tabular-nums">{sponsor != null ? `${sponsor}%` : '—'}</span>
            </span>
            <span className="text-[#4d9eff]">
              Public <span className="tabular-nums">{publicPct != null ? `${publicPct}%` : '—'}</span>
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
