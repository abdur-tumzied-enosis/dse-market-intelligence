import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

const ACCESS_COOKIE = 'dse_access_token'
const REFRESH_COOKIE = 'dse_refresh_token'
const PUBLIC_PATHS = new Set(['/', '/login', '/register'])

export function middleware(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl
  // A session is alive if EITHER cookie is present. The access cookie is
  // short-lived and expires well before the 7-day refresh cookie; the API
  // client silently refreshes it on the first 401. Gating on the access cookie
  // alone bounced users to /login on navigation despite a valid refresh token.
  const token =
    request.cookies.get(ACCESS_COOKIE)?.value ??
    request.cookies.get(REFRESH_COOKIE)?.value

  if (PUBLIC_PATHS.has(pathname)) {
    if (token && (pathname === '/login' || pathname === '/register')) {
      return NextResponse.redirect(new URL('/dashboard', request.url))
    }
    return NextResponse.next()
  }

  if (!token) {
    const loginUrl = new URL('/login', request.url)
    loginUrl.searchParams.set('from', pathname)
    return NextResponse.redirect(loginUrl)
  }

  return NextResponse.next()
}

export const config = {
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico).*)'],
}
