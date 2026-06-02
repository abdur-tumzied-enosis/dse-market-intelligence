import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import LogoutButton from '@/components/layout/LogoutButton'

const mockPush = jest.fn()

jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush }),
}))
jest.mock('@/lib/auth', () => ({
  clearTokens: jest.fn(),
}))
jest.mock('@/components/ui/button', () => ({
  Button: ({ children, ...props }: React.PropsWithChildren<React.ButtonHTMLAttributes<HTMLButtonElement>>) => (
    <button {...props}>{children}</button>
  ),
}))

import { clearTokens } from '@/lib/auth'

beforeEach(() => jest.clearAllMocks())

it('renders a log out button', () => {
  render(<LogoutButton />)
  expect(screen.getByRole('button', { name: /log out/i })).toBeInTheDocument()
})

it('clears tokens and redirects to /login on click', async () => {
  const user = userEvent.setup()
  render(<LogoutButton />)
  await user.click(screen.getByRole('button', { name: /log out/i }))

  await waitFor(() => {
    expect(clearTokens).toHaveBeenCalledTimes(1)
    expect(mockPush).toHaveBeenCalledWith('/login')
  })
})
