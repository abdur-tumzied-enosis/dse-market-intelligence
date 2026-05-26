import { clearTokens, getAccessToken, getRefreshToken, setTokens } from '@/lib/auth'

beforeEach(() => {
  // Clear cookies between tests
  document.cookie = 'dse_access_token=; max-age=0; path=/'
  document.cookie = 'dse_refresh_token=; max-age=0; path=/'
})

it('setTokens stores access token as readable cookie', () => {
  setTokens('access123', 'refresh456')
  expect(getAccessToken()).toBe('access123')
})

it('setTokens stores refresh token as readable cookie', () => {
  setTokens('access123', 'refresh456')
  expect(getRefreshToken()).toBe('refresh456')
})

it('clearTokens removes access token', () => {
  setTokens('access123', 'refresh456')
  clearTokens()
  expect(getAccessToken()).toBeNull()
})

it('clearTokens removes refresh token', () => {
  setTokens('access123', 'refresh456')
  clearTokens()
  expect(getRefreshToken()).toBeNull()
})
