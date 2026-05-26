'use client'

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { clearTokens } from '@/lib/auth'

const NAV_LINKS = [
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/stocks', label: 'Stocks' },
  { href: '/sectors', label: 'Sectors' },
  { href: '/chat', label: 'AI Chat' },
  { href: '/portfolio', label: 'Portfolio' },
  { href: '/reports', label: 'Reports' },
  { href: '/settings', label: 'Settings' },
]

export default function Sidebar() {
  const pathname = usePathname()
  const router = useRouter()

  function handleLogout() {
    clearTokens()
    router.push('/login')
  }

  return (
    <aside className="w-56 shrink-0 min-h-screen bg-surface border-r border-border flex flex-col">
      <div className="px-5 py-4 border-b border-border">
        <span className="font-bold text-white text-lg">DSE Intel</span>
      </div>
      <nav className="flex-1 py-4">
        {NAV_LINKS.map(({ href, label }) => (
          <Link
            key={href}
            href={href}
            className={`flex items-center px-5 py-2.5 text-sm transition-colors ${
              pathname === href || pathname.startsWith(href + '/')
                ? 'bg-elevated text-white'
                : 'text-muted hover:text-white hover:bg-elevated'
            }`}
          >
            {label}
          </Link>
        ))}
      </nav>
      <div className="px-5 py-4 border-t border-border">
        <button
          onClick={handleLogout}
          className="w-full text-left text-sm text-muted hover:text-accent-red transition-colors"
        >
          Sign out
        </button>
      </div>
    </aside>
  )
}
