# Phase A: Consumer Frontend Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Next.js 16 consumer frontend: project scaffold with Tailwind v4 + shadcn, auth helpers, typed API client, login/register pages wired to the real API, app shell (sidebar + topbar layout), and Next.js Edge middleware for route protection.

**Architecture:** Next.js 16.2.6 App Router. Route groups `(auth)/` (no nav, centered card) and `(app)/` (sidebar + topbar). Access token stored in a browser cookie (readable by Next.js Edge middleware). API client auto-refreshes on 401. Consumer API lives at `http://localhost:8000` — backend is already complete.

**Tech Stack:** Next.js 16.2.6, TypeScript, Tailwind CSS v4, shadcn/ui (dark / slate), Jest 29+, @testing-library/react, @testing-library/user-event

---

## Context: What Already Exists

The consumer API (`api/`) is fully built and runs on port 8000. Relevant endpoints for Phase A:

```
POST /api/auth/login     body: {email, password}                → {access_token, refresh_token, token_type}
POST /api/auth/register  body: {email, password, full_name?}    → {id, email, full_name, tier}  (201)
POST /api/auth/refresh   body: {refresh_token}                  → {access_token, refresh_token, token_type}
```

`frontend/` does **not** exist yet — this plan creates it from scratch.

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `frontend/package.json` | Create (scaffold) | Next.js 16.2.6 + all deps |
| `frontend/next.config.ts` | Create (scaffold) | Next.js config |
| `frontend/postcss.config.mjs` | Modify after scaffold | Tailwind v4: `@tailwindcss/postcss` |
| `frontend/jest.config.ts` | Create | Jest with `next/jest` transformer |
| `frontend/jest.setup.ts` | Create | `@testing-library/jest-dom` |
| `frontend/.env.local` | Create | `NEXT_PUBLIC_API_BASE=http://localhost:8000` |
| `frontend/app/globals.css` | Modify after scaffold | `@import "tailwindcss"` + `@theme` design tokens |
| `frontend/app/layout.tsx` | Modify after scaffold | Root layout: dark bg, Inter font |
| `frontend/app/page.tsx` | Modify after scaffold | Root → `redirect('/dashboard')` |
| `frontend/app/(auth)/layout.tsx` | Create | Wraps auth pages in centered card |
| `frontend/app/(auth)/login/page.tsx` | Create | Login form — client component |
| `frontend/app/(auth)/register/page.tsx` | Create | Register form — client component |
| `frontend/app/(app)/layout.tsx` | Create | Sidebar + TopBar wrapper |
| `frontend/app/(app)/dashboard/page.tsx` | Create | Placeholder for Phase B |
| `frontend/components/layout/AuthLayout.tsx` | Create | Centered card for auth pages |
| `frontend/components/layout/Sidebar.tsx` | Create | Left nav, logout button |
| `frontend/components/layout/TopBar.tsx` | Create | Top bar strip |
| `frontend/lib/auth.ts` | Create | Cookie-based token helpers |
| `frontend/lib/api.ts` | Create | Typed fetch wrapper, 401 auto-refresh |
| `frontend/middleware.ts` | Create | Edge: unauthenticated → `/login` |
| `frontend/__tests__/lib/auth.test.ts` | Create | 4 token helper tests |
| `frontend/__tests__/lib/api.test.ts` | Create | 5 API client tests |
| `frontend/__tests__/app/login.test.tsx` | Create | 3 login form tests |
| `frontend/__tests__/app/register.test.tsx` | Create | 3 register form tests |
| `frontend/__tests__/middleware.test.ts` | Create | 3 middleware redirect tests |

---

## Task 1: Project Scaffold

**Files:** All `frontend/` scaffold files, then modify postcss + add jest + .env.local

> This task sets up the bare project. No logic yet. Runs `npm run build` at the end to verify.

- [ ] **Step 1: Create Next.js project from repo root**

Run this from `D:\projects\bdmarcket-analysis`:

```bash
npx create-next-app@16.2.6 frontend \
  --typescript \
  --eslint \
  --tailwind \
  --no-src-dir \
  --app \
  --import-alias "@/*"
```

When prompted interactively, accept all defaults (TypeScript yes, ESLint yes, Tailwind yes, src/ no, App Router yes, alias `@/*`).

- [ ] **Step 2: Upgrade Tailwind to v4 and remove v3 artifacts**

```bash
cd frontend
npm install tailwindcss@^4.0.0 @tailwindcss/postcss
```

Delete `tailwind.config.ts` — Tailwind v4 is configured in CSS, not JS:

```bash
# PowerShell
Remove-Item tailwind.config.ts -ErrorAction SilentlyContinue
```

- [ ] **Step 3: Replace postcss.config.mjs with v4 plugin**

Replace the entire contents of `frontend/postcss.config.mjs`:

```js
export default {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};
```

- [ ] **Step 4: Install shadcn/ui**

```bash
npx shadcn@latest init
```

When prompted:
- Style: **Default**
- Base color: **Slate**
- CSS variables: **Yes**

Then add the three components used in auth forms:

```bash
npx shadcn@latest add button input label
```

- [ ] **Step 5: Install Jest + React Testing Library**

```bash
npm install -D jest jest-environment-jsdom @testing-library/react @testing-library/dom @testing-library/user-event @types/jest
```

- [ ] **Step 6: Create `frontend/jest.config.ts`**

```typescript
import type { Config } from 'jest'
import nextJest from 'next/jest.js'

const createJestConfig = nextJest({ dir: './' })

const config: Config = {
  testEnvironment: 'jsdom',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.ts'],
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/$1',
  },
}

export default createJestConfig(config)
```

- [ ] **Step 7: Create `frontend/jest.setup.ts`**

```typescript
import '@testing-library/jest-dom'
```

- [ ] **Step 8: Add test script to `frontend/package.json`**

Add to the `"scripts"` section:

```json
"test": "jest --passWithNoTests",
"test:watch": "jest --watch"
```

- [ ] **Step 9: Create `frontend/.env.local`**

```
NEXT_PUBLIC_API_BASE=http://localhost:8000
```

- [ ] **Step 10: Verify build and tests pass**

```bash
npm run build
```
Expected: `✓ Compiled successfully` (or similar — no errors).

```bash
npm test
```
Expected: `Test Suites: 0 passed` or similar clean exit (no test files yet).

- [ ] **Step 11: Commit**

```bash
cd D:\projects\bdmarcket-analysis
git add frontend/
git commit -m "feat(frontend): scaffold Next.js 16.2.6 with Tailwind v4, shadcn, Jest"
```

---

## Task 2: Design Tokens + Root Layout

**Files:**
- Modify: `frontend/app/globals.css`
- Modify: `frontend/app/layout.tsx`
- Modify: `frontend/app/page.tsx`

> Adds the colour system from the design spec and wires up the root HTML shell. No tests — pure CSS and layout with no business logic.

- [ ] **Step 1: Replace `frontend/app/globals.css`**

The `create-next-app` scaffold generates a `globals.css` with Tailwind v3 directives. Replace it entirely:

```css
@import "tailwindcss";

/* shadcn-generated variables are appended here by `npx shadcn init` */
/* Our design tokens: */
@theme {
  --color-base: #0a0a0f;
  --color-surface: #111118;
  --color-elevated: #1a1a24;
  --color-border: #2a2a3a;
  --color-muted: #6b6b80;
  --color-accent-green: #00d4a4;
  --color-accent-red: #ff4d6a;
  --color-accent-blue: #4d9eff;
  --color-accent-gold: #f5c842;
}

/* Tailwind v4 generates bg-base, bg-surface, bg-elevated, bg-border,
   text-muted, text-accent-green, text-accent-red, text-accent-blue,
   text-accent-gold, border-border etc. from the @theme block above. */

html {
  color-scheme: dark;
}

body {
  background-color: #0a0a0f;
  color: #e8e8f0;
  font-family: var(--font-inter), system-ui, sans-serif;
}
```

**Note:** If `npx shadcn init` added `@layer base { :root { ... } }` blocks to this file, keep them — they define shadcn's own `--background`, `--foreground`, etc. Just insert the `@theme` block and `html/body` rules after the existing shadcn CSS.

- [ ] **Step 2: Replace `frontend/app/layout.tsx`**

```tsx
import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' })

export const metadata: Metadata = {
  title: 'DSE Stock Intelligence',
  description: 'Market data and analysis for Dhaka Stock Exchange',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} dark`} suppressHydrationWarning>
      <body>{children}</body>
    </html>
  )
}
```

- [ ] **Step 3: Replace `frontend/app/page.tsx`**

```tsx
import { redirect } from 'next/navigation'

export default function RootPage() {
  redirect('/dashboard')
}
```

- [ ] **Step 4: Verify build still passes**

```bash
cd frontend
npm run build
```
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
cd D:\projects\bdmarcket-analysis
git add frontend/app/globals.css frontend/app/layout.tsx frontend/app/page.tsx
git commit -m "feat(frontend): design tokens, root layout, root → /dashboard redirect"
```

---

## Task 3: Auth Helpers (`lib/auth.ts`)

**Files:**
- Create: `frontend/lib/auth.ts`
- Create: `frontend/__tests__/lib/auth.test.ts`

> Thin wrappers over `document.cookie`. Next.js Edge middleware reads these same cookies from `request.cookies`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/__tests__/lib/auth.test.ts`:

```typescript
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
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
cd frontend
npm test -- __tests__/lib/auth.test.ts
```
Expected: FAIL — `Cannot find module '@/lib/auth'`

- [ ] **Step 3: Create `frontend/lib/auth.ts`**

```typescript
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
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
npm test -- __tests__/lib/auth.test.ts
```
Expected: 4 passed, 0 failed.

- [ ] **Step 5: Commit**

```bash
cd D:\projects\bdmarcket-analysis
git add frontend/lib/auth.ts frontend/__tests__/lib/auth.test.ts
git commit -m "feat(frontend): auth token helpers (cookie-based)"
```

---

## Task 4: API Client (`lib/api.ts`)

**Files:**
- Create: `frontend/lib/api.ts`
- Create: `frontend/__tests__/lib/api.test.ts`

> Typed fetch wrapper. Automatically attaches `Authorization: Bearer <token>`. On 401, attempts a silent token refresh and retries once. Clears tokens and throws `'Unauthorized'` if refresh also fails.

- [ ] **Step 1: Write the failing tests**

Create `frontend/__tests__/lib/api.test.ts`:

```typescript
import { api, get } from '@/lib/api'
import * as auth from '@/lib/auth'

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
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
cd frontend
npm test -- __tests__/lib/api.test.ts
```
Expected: FAIL — `Cannot find module '@/lib/api'`

- [ ] **Step 3: Create `frontend/lib/api.ts`**

```typescript
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from '@/lib/auth'

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
}
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
npm test -- __tests__/lib/api.test.ts
```
Expected: 5 passed, 0 failed.

- [ ] **Step 5: Commit**

```bash
cd D:\projects\bdmarcket-analysis
git add frontend/lib/api.ts frontend/__tests__/lib/api.test.ts
git commit -m "feat(frontend): typed API client with 401 auto-refresh"
```

---

## Task 5: Auth Route Group + Login Page

**Files:**
- Create: `frontend/components/layout/AuthLayout.tsx`
- Create: `frontend/app/(auth)/layout.tsx`
- Create: `frontend/app/(auth)/login/page.tsx`
- Create: `frontend/__tests__/app/login.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/__tests__/app/login.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import LoginPage from '@/app/(auth)/login/page'

const mockPush = jest.fn()

jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush }),
}))
jest.mock('@/lib/api', () => ({
  api: { auth: { login: jest.fn() } },
}))
jest.mock('@/lib/auth', () => ({
  setTokens: jest.fn(),
}))
// Stub shadcn components so tests don't need a full CSS build
jest.mock('@/components/ui/button', () => ({
  Button: ({ children, ...props }: React.PropsWithChildren<React.ButtonHTMLAttributes<HTMLButtonElement>>) => (
    <button {...props}>{children}</button>
  ),
}))
jest.mock('@/components/ui/input', () => ({
  Input: (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
}))
jest.mock('@/components/ui/label', () => ({
  Label: ({ children, ...props }: React.PropsWithChildren<React.LabelHTMLAttributes<HTMLLabelElement>>) => (
    <label {...props}>{children}</label>
  ),
}))

import { api } from '@/lib/api'
import { setTokens } from '@/lib/auth'

beforeEach(() => jest.clearAllMocks())

it('renders email and password inputs', () => {
  render(<LoginPage />)
  expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
  expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
})

it('calls api.auth.login, stores tokens, redirects to /dashboard on success', async () => {
  const user = userEvent.setup()
  ;(api.auth.login as jest.Mock).mockResolvedValueOnce({
    access_token: 'at',
    refresh_token: 'rt',
    token_type: 'bearer',
  })

  render(<LoginPage />)
  await user.type(screen.getByLabelText(/email/i), 'test@test.com')
  await user.type(screen.getByLabelText(/password/i), 'password123')
  await user.click(screen.getByRole('button', { name: /sign in/i }))

  await waitFor(() => {
    expect(api.auth.login).toHaveBeenCalledWith('test@test.com', 'password123')
    expect(setTokens).toHaveBeenCalledWith('at', 'rt')
    expect(mockPush).toHaveBeenCalledWith('/dashboard')
  })
})

it('shows error alert when login fails', async () => {
  const user = userEvent.setup()
  ;(api.auth.login as jest.Mock).mockRejectedValueOnce(new Error('Invalid credentials'))

  render(<LoginPage />)
  await user.type(screen.getByLabelText(/email/i), 'bad@test.com')
  await user.type(screen.getByLabelText(/password/i), 'wrongpass')
  await user.click(screen.getByRole('button', { name: /sign in/i }))

  await waitFor(() => {
    expect(screen.getByRole('alert')).toHaveTextContent('Invalid credentials')
  })
})
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
cd frontend
npm test -- __tests__/app/login.test.tsx
```
Expected: FAIL — `Cannot find module '@/app/(auth)/login/page'`

- [ ] **Step 3: Create `frontend/components/layout/AuthLayout.tsx`**

```tsx
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-base p-4">
      <div className="w-full max-w-md bg-surface rounded-lg border border-border p-8">
        {children}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Create `frontend/app/(auth)/layout.tsx`**

```tsx
import AuthLayout from '@/components/layout/AuthLayout'

export default function AuthRouteLayout({ children }: { children: React.ReactNode }) {
  return <AuthLayout>{children}</AuthLayout>
}
```

- [ ] **Step 5: Create `frontend/app/(auth)/login/page.tsx`**

```tsx
'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { api } from '@/lib/api'
import { setTokens } from '@/lib/auth'

export default function LoginPage() {
  const router = useRouter()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const tokens = await api.auth.login(email, password)
      setTokens(tokens.access_token, tokens.refresh_token)
      router.push('/dashboard')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <h1 className="text-2xl font-bold text-white mb-2">Sign in</h1>
      <p className="text-muted text-sm mb-6">DSE Stock Intelligence Platform</p>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-1">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
            placeholder="you@example.com"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
          />
        </div>
        {error && (
          <p role="alert" className="text-accent-red text-sm">
            {error}
          </p>
        )}
        <Button type="submit" className="w-full" disabled={loading}>
          {loading ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>
      <p className="text-center text-sm text-muted mt-6">
        No account?{' '}
        <Link href="/register" className="text-accent-blue hover:underline">
          Register
        </Link>
      </p>
    </>
  )
}
```

- [ ] **Step 6: Run tests — confirm they pass**

```bash
npm test -- __tests__/app/login.test.tsx
```
Expected: 3 passed, 0 failed.

- [ ] **Step 7: Commit**

```bash
cd D:\projects\bdmarcket-analysis
git add frontend/components/layout/AuthLayout.tsx \
        frontend/app/\(auth\)/layout.tsx \
        frontend/app/\(auth\)/login/page.tsx \
        frontend/__tests__/app/login.test.tsx
git commit -m "feat(frontend): auth layout + login page"
```

---

## Task 6: Register Page

**Files:**
- Create: `frontend/app/(auth)/register/page.tsx`
- Create: `frontend/__tests__/app/register.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/__tests__/app/register.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import RegisterPage from '@/app/(auth)/register/page'

const mockPush = jest.fn()

jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush }),
}))
jest.mock('@/lib/api', () => ({
  api: { auth: { register: jest.fn(), login: jest.fn() } },
}))
jest.mock('@/lib/auth', () => ({
  setTokens: jest.fn(),
}))
jest.mock('@/components/ui/button', () => ({
  Button: ({ children, ...props }: React.PropsWithChildren<React.ButtonHTMLAttributes<HTMLButtonElement>>) => (
    <button {...props}>{children}</button>
  ),
}))
jest.mock('@/components/ui/input', () => ({
  Input: (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
}))
jest.mock('@/components/ui/label', () => ({
  Label: ({ children, ...props }: React.PropsWithChildren<React.LabelHTMLAttributes<HTMLLabelElement>>) => (
    <label {...props}>{children}</label>
  ),
}))

import { api } from '@/lib/api'
import { setTokens } from '@/lib/auth'

beforeEach(() => jest.clearAllMocks())

it('renders email and password inputs', () => {
  render(<RegisterPage />)
  expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
  expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
})

it('registers, auto-logs in, stores tokens, redirects to /dashboard', async () => {
  const user = userEvent.setup()
  ;(api.auth.register as jest.Mock).mockResolvedValueOnce({
    id: 1,
    email: 'a@b.com',
    full_name: null,
    tier: 'free',
  })
  ;(api.auth.login as jest.Mock).mockResolvedValueOnce({
    access_token: 'at',
    refresh_token: 'rt',
    token_type: 'bearer',
  })

  render(<RegisterPage />)
  await user.type(screen.getByLabelText(/email/i), 'a@b.com')
  await user.type(screen.getByLabelText(/password/i), 'password123')
  await user.click(screen.getByRole('button', { name: /create account/i }))

  await waitFor(() => {
    expect(api.auth.register).toHaveBeenCalledWith('a@b.com', 'password123', undefined)
    expect(api.auth.login).toHaveBeenCalledWith('a@b.com', 'password123')
    expect(setTokens).toHaveBeenCalledWith('at', 'rt')
    expect(mockPush).toHaveBeenCalledWith('/dashboard')
  })
})

it('shows error alert when email is already registered', async () => {
  const user = userEvent.setup()
  ;(api.auth.register as jest.Mock).mockRejectedValueOnce(new Error('Email already registered'))

  render(<RegisterPage />)
  await user.type(screen.getByLabelText(/email/i), 'existing@b.com')
  await user.type(screen.getByLabelText(/password/i), 'password123')
  await user.click(screen.getByRole('button', { name: /create account/i }))

  await waitFor(() => {
    expect(screen.getByRole('alert')).toHaveTextContent('Email already registered')
  })
})
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
cd frontend
npm test -- __tests__/app/register.test.tsx
```
Expected: FAIL — `Cannot find module '@/app/(auth)/register/page'`

- [ ] **Step 3: Create `frontend/app/(auth)/register/page.tsx`**

```tsx
'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { api } from '@/lib/api'
import { setTokens } from '@/lib/auth'

export default function RegisterPage() {
  const router = useRouter()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await api.auth.register(email, password, fullName || undefined)
      const tokens = await api.auth.login(email, password)
      setTokens(tokens.access_token, tokens.refresh_token)
      router.push('/dashboard')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <h1 className="text-2xl font-bold text-white mb-2">Create account</h1>
      <p className="text-muted text-sm mb-6">Free tier — upgrade to Pro anytime</p>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-1">
          <Label htmlFor="fullName">Full name (optional)</Label>
          <Input
            id="fullName"
            type="text"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            autoComplete="name"
            placeholder="Your name"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
            placeholder="you@example.com"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="new-password"
            placeholder="Minimum 8 characters"
          />
        </div>
        {error && (
          <p role="alert" className="text-accent-red text-sm">
            {error}
          </p>
        )}
        <Button type="submit" className="w-full" disabled={loading}>
          {loading ? 'Creating account…' : 'Create account'}
        </Button>
      </form>
      <p className="text-center text-sm text-muted mt-6">
        Already have an account?{' '}
        <Link href="/login" className="text-accent-blue hover:underline">
          Sign in
        </Link>
      </p>
    </>
  )
}
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
npm test -- __tests__/app/register.test.tsx
```
Expected: 3 passed, 0 failed.

- [ ] **Step 5: Commit**

```bash
cd D:\projects\bdmarcket-analysis
git add frontend/app/\(auth\)/register/page.tsx \
        frontend/__tests__/app/register.test.tsx
git commit -m "feat(frontend): register page with auto-login"
```

---

## Task 7: App Shell (Sidebar + TopBar + App Layout)

**Files:**
- Create: `frontend/components/layout/Sidebar.tsx`
- Create: `frontend/components/layout/TopBar.tsx`
- Create: `frontend/app/(app)/layout.tsx`
- Create: `frontend/app/(app)/dashboard/page.tsx`

> Presentational components — no business logic, no tests needed in Phase A. Dashboard page is a placeholder for Phase B.

- [ ] **Step 1: Create `frontend/components/layout/Sidebar.tsx`**

```tsx
'use client'

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { clearTokens } from '@/lib/auth'

const NAV_LINKS = [
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/stocks', label: 'Stocks' },
  { href: '/sectors', label: 'Sectors' },
  { href: '/chat', label: 'AI Chat' },
  { href: '/portfolio', label: 'Portfolio' },
  { href: '/reports', label: 'Reports' },
  { href: '/settings', label: 'Settings' },
]

export default function Sidebar() {
  const pathname = usePathname()
  const router = useRouter()

  function handleLogout() {
    clearTokens()
    router.push('/login')
  }

  return (
    <aside className="w-56 shrink-0 min-h-screen bg-surface border-r border-border flex flex-col">
      <div className="px-5 py-4 border-b border-border">
        <span className="font-bold text-white text-lg">DSE Intel</span>
      </div>
      <nav className="flex-1 py-4">
        {NAV_LINKS.map(({ href, label }) => (
          <Link
            key={href}
            href={href}
            className={`flex items-center px-5 py-2.5 text-sm transition-colors ${
              pathname.startsWith(href)
                ? 'bg-elevated text-white'
                : 'text-muted hover:text-white hover:bg-elevated'
            }`}
          >
            {label}
          </Link>
        ))}
      </nav>
      <div className="px-5 py-4 border-t border-border">
        <button
          onClick={handleLogout}
          className="w-full text-left text-sm text-muted hover:text-accent-red transition-colors"
        >
          Sign out
        </button>
      </div>
    </aside>
  )
}
```

- [ ] **Step 2: Create `frontend/components/layout/TopBar.tsx`**

```tsx
export default function TopBar() {
  return (
    <header className="h-14 border-b border-border bg-surface flex items-center px-6">
      <span className="text-sm text-muted">DSE Stock Intelligence Platform</span>
    </header>
  )
}
```

- [ ] **Step 3: Create `frontend/app/(app)/layout.tsx`**

```tsx
import Sidebar from '@/components/layout/Sidebar'
import TopBar from '@/components/layout/TopBar'

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen bg-base">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0">
        <TopBar />
        <main className="flex-1 p-6">{children}</main>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Create `frontend/app/(app)/dashboard/page.tsx`**

```tsx
export default function DashboardPage() {
  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-2">Dashboard</h1>
      <p className="text-muted text-sm">Market overview coming in Phase B.</p>
    </div>
  )
}
```

- [ ] **Step 5: Verify build**

```bash
cd frontend
npm run build
```
Expected: no errors. Next.js will list all routes — you should see `/`, `/login`, `/register`, `/dashboard`.

- [ ] **Step 6: Commit**

```bash
cd D:\projects\bdmarcket-analysis
git add frontend/components/layout/Sidebar.tsx \
        frontend/components/layout/TopBar.tsx \
        frontend/app/\(app\)/layout.tsx \
        frontend/app/\(app\)/dashboard/page.tsx
git commit -m "feat(frontend): app shell — Sidebar, TopBar, app layout, dashboard placeholder"
```

---

## Task 8: Middleware + Full Test Suite

**Files:**
- Create: `frontend/middleware.ts`
- Create: `frontend/__tests__/middleware.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `frontend/__tests__/middleware.test.ts`:

```typescript
import { middleware } from '@/middleware'
import { NextRequest } from 'next/server'

function makeReq(pathname: string, token?: string): NextRequest {
  const req = new NextRequest(`http://localhost:3000${pathname}`)
  if (token) req.cookies.set('dse_access_token', token)
  return req
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
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
cd frontend
npm test -- __tests__/middleware.test.ts
```
Expected: FAIL — `Cannot find module '@/middleware'`

- [ ] **Step 3: Create `frontend/middleware.ts`**

```typescript
import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

const ACCESS_COOKIE = 'dse_access_token'
const PUBLIC_PATHS = new Set(['/', '/login', '/register'])

export function middleware(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl
  const token = request.cookies.get(ACCESS_COOKIE)?.value

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
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
npm test -- __tests__/middleware.test.ts
```
Expected: 3 passed, 0 failed.

- [ ] **Step 5: Run the full test suite**

```bash
npm test
```
Expected: **18 tests passed** across 5 test suites, 0 failed.

- [ ] **Step 6: Commit**

```bash
cd D:\projects\bdmarcket-analysis
git add frontend/middleware.ts frontend/__tests__/middleware.test.ts
git commit -m "feat(frontend): Edge middleware — unauthenticated redirect to /login"
```

---

## Manual Verification (after all tasks complete)

Start the consumer API (requires Docker stack running):

```bash
# From project root — start DB + Redis
make up

# Start consumer API (separate terminal)
python -m uvicorn api.main:app --reload --port 8000
```

Start the frontend:

```bash
cd frontend
npm run dev
# → http://localhost:3000
```

Test the golden path:

1. Visit `http://localhost:3000` → redirects to `/login` (not authenticated)
2. Register at `/register` → fills email + password → redirects to `/dashboard` on success
3. Visit `/dashboard` → renders "Dashboard — Market overview coming in Phase B."
4. Click "Sign out" → redirects to `/login`
5. Log in at `/login` → redirects to `/dashboard`
6. Directly visit `/login` while authenticated → redirects to `/dashboard`
