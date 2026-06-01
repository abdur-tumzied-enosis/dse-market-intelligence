import { decodeTier } from '@/lib/jwt'

function makeToken(payload: object): string {
  const b64 = (o: object) =>
    Buffer.from(JSON.stringify(o)).toString('base64url')
  return `${b64({ alg: 'HS256' })}.${b64(payload)}.sig`
}

describe('decodeTier', () => {
  it('reads the tier claim from a token', () => {
    expect(decodeTier(makeToken({ sub: '1', tier: 'pro' }))).toBe('pro')
  })
  it('defaults to free when claim missing', () => {
    expect(decodeTier(makeToken({ sub: '1' }))).toBe('free')
  })
  it('defaults to free for null or malformed tokens', () => {
    expect(decodeTier(null)).toBe('free')
    expect(decodeTier('garbage')).toBe('free')
  })
})
