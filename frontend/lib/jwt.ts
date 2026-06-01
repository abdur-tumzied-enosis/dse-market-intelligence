// frontend/lib/jwt.ts
// UI-only tier read. The server is the source of truth for enforcement;
// this decode is NOT signature-verified and must never gate real access.
export type Tier = 'free' | 'pro'

export function decodeTier(token: string | null | undefined): Tier {
  if (!token) return 'free'
  try {
    const payload = token.split('.')[1]
    if (!payload) return 'free'
    const json =
      typeof atob === 'function'
        ? atob(payload.replace(/-/g, '+').replace(/_/g, '/'))
        : Buffer.from(payload, 'base64').toString('utf-8')
    const tier = JSON.parse(json).tier
    return tier === 'pro' ? 'pro' : 'free'
  } catch {
    return 'free'
  }
}
