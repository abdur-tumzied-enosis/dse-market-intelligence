'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  CommandDialog,
  CommandInput,
  CommandList,
  CommandGroup,
  CommandItem,
} from '@/components/ui/command'
import { api } from '@/lib/api'
import type { StockListItem } from '@/lib/types'

export default function CommandPalette() {
  const router = useRouter()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<StockListItem[]>([])
  const [loading, setLoading] = useState(false)
  const seq = useRef(0)

  // Open via Ctrl/Cmd+K or the `open-command-palette` window event.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((o) => !o)
      }
    }
    function onOpenEvent() {
      setOpen(true)
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('open-command-palette', onOpenEvent)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('open-command-palette', onOpenEvent)
    }
  }, [])

  // Debounced server search; ignore out-of-order responses via a sequence ref.
  // All setState calls are deferred into setTimeout callbacks to satisfy the
  // react-hooks/set-state-in-effect lint rule.
  useEffect(() => {
    const q = query.trim()
    if (!q) {
      const id = setTimeout(() => {
        setResults([])
        setLoading(false)
      }, 0)
      return () => clearTimeout(id)
    }
    const mySeq = ++seq.current
    const t = setTimeout(() => {
      setLoading(true)
      api.stocks
        .list({ q, limit: 5 })
        .then((res) => {
          if (mySeq === seq.current) setResults(res.items)
        })
        .catch(() => {
          if (mySeq === seq.current) setResults([])
        })
        .finally(() => {
          if (mySeq === seq.current) setLoading(false)
        })
    }, 200)
    return () => clearTimeout(t)
  }, [query])

  const onSelect = useCallback(
    (ticker: string) => {
      setOpen(false)
      setQuery('')
      setResults([])
      router.push(`/stocks/${ticker}`)
    },
    [router],
  )

  const trimmed = query.trim()

  return (
    <CommandDialog open={open} onOpenChange={setOpen} shouldFilter={false}>
      <CommandInput
        placeholder="Search stocks by ticker or name…"
        value={query}
        onValueChange={setQuery}
      />
      <CommandList>
        {!trimmed && (
          <div className="py-6 text-center text-sm text-[#6b6b80]">Type to search stocks…</div>
        )}
        {trimmed && loading && (
          <div className="py-6 text-center text-sm text-[#6b6b80]">Searching…</div>
        )}
        {trimmed && !loading && results.length === 0 && (
          <div className="py-6 text-center text-sm text-[#6b6b80]">No stocks match.</div>
        )}
        {results.length > 0 && (
          <CommandGroup heading="Stocks">
            {results.map((s) => (
              <CommandItem key={s.ticker} value={s.ticker} onSelect={() => onSelect(s.ticker)}>
                <span className="font-mono font-bold text-[#4d9eff] mr-2">{s.ticker}</span>
                <span className="flex-1 truncate text-[#e8e8f0]">{s.name}</span>
                <span className="ml-2 text-[11px] font-mono text-[#6b6b80]">{s.sector}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
      </CommandList>
    </CommandDialog>
  )
}
