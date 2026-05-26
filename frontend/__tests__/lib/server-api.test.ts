// @jest-environment node
// frontend/__tests__/lib/server-api.test.ts
import { cookies } from 'next/headers'
import { serverGet } from '@/lib/server-api'

jest.mock('next/headers', () => ({
  cookies: jest.fn(),
}))

const mockCookies = cookies as jest.Mock

global.fetch = jest.fn()

beforeEach(() => {
  jest.clearAllMocks()
  mockCookies.mockResolvedValue({
    get: jest.fn().mockReturnValue({ value: 'test-token-123' }),
  })
})

test('serverGet passes Authorization header when token in cookie', async () => {
  ;(global.fetch as jest.Mock).mockResolvedValue({
    ok: true,
    json: async () => ({ data: 'ok' }),
  })

  const result = await serverGet('/api/market/indices')

  expect(global.fetch).toHaveBeenCalledWith(
    expect.stringContaining('/api/market/indices'),
    expect.objectContaining({
      headers: expect.objectContaining({ Authorization: 'Bearer test-token-123' }),
    }),
  )
  expect(result).toEqual({ data: 'ok' })
})

test('serverGet throws on non-ok response', async () => {
  ;(global.fetch as jest.Mock).mockResolvedValue({
    ok: false,
    status: 503,
  })

  await expect(serverGet('/api/market/indices')).rejects.toThrow('HTTP 503')
})

test('serverGet omits Authorization if no cookie', async () => {
  mockCookies.mockResolvedValue({
    get: jest.fn().mockReturnValue(undefined),
  })
  ;(global.fetch as jest.Mock).mockResolvedValue({
    ok: true,
    json: async () => ({}),
  })

  await serverGet('/api/market/summary')

  const call = (global.fetch as jest.Mock).mock.calls[0]
  expect(call[1].headers.Authorization).toBeUndefined()
})
