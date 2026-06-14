import { NextResponse, type NextRequest } from 'next/server'

// Clears the auth cookies and bounces to /login. Reached when SSR detects a
// dead session (access token expired AND refresh token rejected). The access
// and refresh cookies are non-HttpOnly and can only be deleted from a Route
// Handler — not during a page render. Without clearing them, middleware (which
// gates purely on cookie presence) would bounce /login → /dashboard → 401 →
// /login forever.
export async function GET(request: NextRequest): Promise<NextResponse> {
  const from = request.nextUrl.searchParams.get('from')
  const loginUrl = new URL('/login', request.url)
  if (from) loginUrl.searchParams.set('from', from)

  const response = NextResponse.redirect(loginUrl)
  response.cookies.delete('dse_access_token')
  response.cookies.delete('dse_refresh_token')
  return response
}
