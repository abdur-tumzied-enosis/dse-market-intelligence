import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import LoginPage from '@/app/(auth)/login/page'

const mockPush = jest.fn()
const mockSearchParams = new URLSearchParams()

jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush }),
  useSearchParams: () => mockSearchParams,
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
  Label: ({ children, htmlFor, className }: { children: React.ReactNode; htmlFor?: string; className?: string }) => (
    <label htmlFor={htmlFor} className={className}>{children}</label>
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

it('redirects to ?from path when present after login', async () => {
  const user = userEvent.setup()
  mockSearchParams.set('from', '/stocks')
  ;(api.auth.login as jest.Mock).mockResolvedValueOnce({
    access_token: 'at',
    refresh_token: 'rt',
    token_type: 'bearer',
  })

  render(<LoginPage />)
  await user.type(screen.getByLabelText(/email/i), 'test@test.com')
  await user.type(screen.getByLabelText(/password/i), 'pass123')
  await user.click(screen.getByRole('button', { name: /sign in/i }))

  await waitFor(() => {
    expect(mockPush).toHaveBeenCalledWith('/stocks')
  })
  mockSearchParams.delete('from') // cleanup for other tests
})
