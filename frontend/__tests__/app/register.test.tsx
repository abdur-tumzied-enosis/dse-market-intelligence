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
  Label: ({ children, htmlFor, className }: { children: React.ReactNode; htmlFor?: string; className?: string }) => (
    <label htmlFor={htmlFor} className={className}>{children}</label>
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
