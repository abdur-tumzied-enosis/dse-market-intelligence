import { api, get } from '@/lib/api'
import * as auth from '@/lib/auth'

jest.mock('@/lib/auth', () => ({
  getAccessToken: jest.fn(),
  getRefreshToken: jest.fn(),
  setTokens: jest.fn(),
  clearTokens: jest.fn(),
}))

global.fetch = jest.fn()

beforeEach(() => {
  jest.clearAllMocks()
})

describe('api.auth.login', () => {
  it('POSTs credentials and returns token pair', async () => {
    const mockTokens = { access_token: 'at', refresh_token: 'rt', token_type: 'bearer' }
    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => mockTokens,
    })

    const result = await api.auth.login('user@test.com', 'pass123')

    expect(global.fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/auth/login',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(result).toEqual(mockTokens)
  })

  it('throws error detail from API on failure', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({ detail: 'Invalid credentials' }),
    })

    await expect(api.auth.login('a@b.com', 'wrong')).rejects.toThrow('Invalid credentials')
  })
})

describe('api.auth.register', () => {
  it('POSTs user data and returns user record', async () => {
    const mockUser = { id: 1, email: 'a@b.com', full_name: null, tier: 'free' }
    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => mockUser,
    })

    const result = await api.auth.register('a@b.com', 'password123')
    expect(result).toEqual(mockUser)
  })
})

describe('get — 401 auto-refresh', () => {
  it('retries with refreshed token after successful refresh', async () => {
    jest.spyOn(auth, 'getRefreshToken').mockReturnValue('old-rt')
    jest.spyOn(auth, 'getAccessToken').mockReturnValue(null)
    const setTokensSpy = jest.spyOn(auth, 'setTokens').mockImplementation(() => {})

    ;(global.fetch as jest.Mock)
      .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({}) })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ access_token: 'new-at', refresh_token: 'new-rt', token_type: 'bearer' }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ data: 'ok' }) })

    const result = await get<{ data: string }>('/api/some/resource')

    expect(result).toEqual({ data: 'ok' })
    expect(setTokensSpy).toHaveBeenCalledWith('new-at', 'new-rt')
  })

  it('clears tokens and throws on refresh failure', async () => {
    jest.spyOn(auth, 'getRefreshToken').mockReturnValue('bad-rt')
    jest.spyOn(auth, 'getAccessToken').mockReturnValue(null)
    const clearSpy = jest.spyOn(auth, 'clearTokens').mockImplementation(() => {})

    ;(global.fetch as jest.Mock)
      .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({}) })
      .mockResolvedValueOnce({ ok: false, json: async () => ({}) })

    await expect(get('/api/some/resource')).rejects.toThrow('Unauthorized')
    expect(clearSpy).toHaveBeenCalled()
  })
})
