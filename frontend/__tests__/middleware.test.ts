/**
 * @jest-environment node
 */
import { middleware } from '@/middleware'
import { NextRequest } from 'next/server'

function makeReq(pathname: string, token?: string): NextRequest {
  return new NextRequest(`http://localhost:3000${pathname}`, {
    headers: token ? { cookie: `dse_access_token=${token}` } : {},
  })
}

it('redirects unauthenticated request to /login', () => {
  const res = middleware(makeReq('/dashboard'))
  expect(res.headers.get('location')).toMatch(/\/login/)
})

it('allows authenticated request to /dashboard (no redirect)', () => {
  const res = middleware(makeReq('/dashboard', 'valid-token'))
  expect(res.headers.get('location')).toBeNull()
})

it('redirects authenticated user away from /login to /dashboard', () => {
  const res = middleware(makeReq('/login', 'valid-token'))
  expect(res.headers.get('location')).toMatch(/\/dashboard/)
})
