const ACCESS_COOKIE = 'dse_access_token'
const REFRESH_COOKIE = 'dse_refresh_token'

function getCookie(name: string): string | null {
  if (typeof document === 'undefined') return null
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`))
  return match ? decodeURIComponent(match[1]) : null
}

function setCookie(name: string, value: string, maxAge: number): void {
  document.cookie = `${name}=${encodeURIComponent(value)}; path=/; max-age=${maxAge}; SameSite=Lax`
}

function deleteCookie(name: string): void {
  document.cookie = `${name}=; path=/; max-age=0`
}

export function setTokens(accessToken: string, refreshToken: string): void {
  setCookie(ACCESS_COOKIE, accessToken, 900)       // 15 min
  setCookie(REFRESH_COOKIE, refreshToken, 604800)  // 7 days
}

export function getAccessToken(): string | null {
  return getCookie(ACCESS_COOKIE)
}

export function getRefreshToken(): string | null {
  return getCookie(REFRESH_COOKIE)
}

export function clearTokens(): void {
  deleteCookie(ACCESS_COOKIE)
  deleteCookie(REFRESH_COOKIE)
}
