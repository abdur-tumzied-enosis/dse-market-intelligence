'use client'

import { Search } from 'lucide-react'

export default function SearchTrigger() {
  return (
    <button
      type="button"
      onClick={() => window.dispatchEvent(new Event('open-command-palette'))}
      className="flex items-center gap-2 min-w-[220px] px-3 py-1.5 rounded-lg bg-[#1a1a24] border border-[#2a2a3a] text-sm text-[#6b6b80] hover:border-[#4d9eff]/50 hover:text-[#e8e8f0] transition-colors"
    >
      <Search className="w-3.5 h-3.5" />
      <span className="flex-1 text-left">Search stocks…</span>
      <kbd className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[#0f0f18] border border-[#2a2a3a]">
        ⌘K
      </kbd>
    </button>
  )
}
