import { clearTokens, getAccessToken, getRefreshToken, setTokens } from '@/lib/auth'
import type { SectorRow } from './types'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000'

async function request<T>(
  path: string,
  init: RequestInit = {},
  retryOn401 = true,
): Promise<T> {
  const token = getAccessToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(init.headers as Record<string, string> ?? {}),
  }

  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })

  if (res.status === 401 && retryOn401) {
    const rt = getRefreshToken()
    if (rt) {
      const rfRes = await fetch(`${API_BASE}/api/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: rt }),
      })
      if (rfRes.ok) {
        const tokens = await rfRes.json()
        setTokens(tokens.access_token, tokens.refresh_token)
        return request<T>(path, init, false)
      }
    }
    clearTokens()
    throw new Error('Unauthorized')
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
  }

  return res.json() as Promise<T>
}

export function get<T>(path: string): Promise<T> {
  return request<T>(path)
}

export function post<T>(path: string, body: unknown, retryOn401 = false): Promise<T> {
  return request<T>(path, { method: 'POST', body: JSON.stringify(body) }, retryOn401)
}

export const fetchSectors = () => get<SectorRow[]>('/api/sectors')

export const api = {
  auth: {
    login: (email: string, password: string) =>
      post<{ access_token: string; refresh_token: string; token_type: string }>(
        '/api/auth/login',
        { email, password },
      ),
    register: (email: string, password: string, fullName?: string) =>
      post<{ id: number; email: string; full_name: string | null; tier: string }>(
        '/api/auth/register',
        { email, password, ...(fullName ? { full_name: fullName } : {}) },
      ),
  },
  stocks: {
    prices: (ticker: string, params: string) =>
      get<import('./types').OHLCVResponse>(`/api/stocks/${ticker}/prices?${params}`),
    announcements: (ticker: string) =>
      get<import('./types').AnnouncementsResponse>(`/api/stocks/${ticker}/announcements`),
  },
}
