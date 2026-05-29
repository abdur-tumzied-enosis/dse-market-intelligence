import Link from 'next/link'

interface Props {
  feature?: string
  children: React.ReactNode
}

export default function PaywallOverlay({ feature, children }: Props) {
  return (
    <div className="relative overflow-hidden rounded-xl">
      <div className="blur-sm pointer-events-none select-none" aria-hidden>
        {children}
      </div>
      <div className="absolute inset-0 flex items-center justify-center bg-[#0a0a0f]/60 backdrop-blur-[2px]">
        <div className="bg-[#1a1a24] border border-[#2a2a3a] rounded-xl p-6 text-center max-w-sm shadow-2xl">
          <div className="w-10 h-10 rounded-full bg-[#f5c842]/15 border border-[#f5c842]/30 flex items-center justify-center mx-auto mb-3">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#f5c842" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
              <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
            </svg>
          </div>
          <h3 className="text-sm font-mono font-semibold text-[#e8e8f0] mb-1">
            Pro Feature
          </h3>
          {feature && (
            <p className="text-[11px] font-mono text-[#6b6b80] mb-3">{feature}</p>
          )}
          <Link
            href="/settings#upgrade"
            className="inline-flex items-center gap-1.5 bg-[#4d9eff]/15 hover:bg-[#4d9eff]/25 text-[#4d9eff] border border-[#4d9eff]/30 text-[11px] font-mono font-medium px-4 py-2 rounded-lg transition-all"
          >
            Upgrade to Pro →
          </Link>
        </div>
      </div>
    </div>
  )
}
