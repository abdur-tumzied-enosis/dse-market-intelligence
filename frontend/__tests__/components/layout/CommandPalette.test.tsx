import { render, screen, fireEvent, act } from '@testing-library/react'
import CommandPalette from '@/components/layout/CommandPalette'

const mockPush = jest.fn()
jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush }),
}))
jest.mock('@/lib/api', () => ({
  api: { stocks: { list: jest.fn() } },
}))
jest.mock('@/components/ui/command', () => ({
  CommandDialog: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? <div role="dialog">{children}</div> : null,
  CommandInput: ({ value, onValueChange, placeholder }: {
    value: string; onValueChange: (v: string) => void; placeholder?: string
  }) => (
    <input aria-label="search" placeholder={placeholder} value={value}
      onChange={(e) => onValueChange(e.target.value)} />
  ),
  CommandList: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  CommandGroup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  CommandItem: ({ children, onSelect }: { children: React.ReactNode; onSelect: () => void }) => (
    <div role="option" onClick={onSelect}>{children}</div>
  ),
}))

import { api } from '@/lib/api'

beforeEach(() => {
  jest.clearAllMocks()
  jest.useFakeTimers()
})
afterEach(() => {
  jest.useRealTimers()
})

test('Ctrl+K opens the palette', () => {
  render(<CommandPalette />)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
  expect(screen.getByRole('dialog')).toBeInTheDocument()
})

test('open-command-palette event opens the palette', () => {
  render(<CommandPalette />)
  act(() => { window.dispatchEvent(new Event('open-command-palette')) })
  expect(screen.getByRole('dialog')).toBeInTheDocument()
})

test('typing fetches and renders results', async () => {
  ;(api.stocks.list as jest.Mock).mockResolvedValue({
    items: [{ ticker: 'GP', name: 'Grameenphone', sector: 'Telecom', category: 'A', market_cap_bdt: 1, is_active: true }],
    total: 1, limit: 5, offset: 0,
  })
  render(<CommandPalette />)
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
  fireEvent.change(screen.getByLabelText('search'), { target: { value: 'gp' } })
  await act(async () => { jest.advanceTimersByTime(250) })
  expect(api.stocks.list).toHaveBeenCalledWith({ q: 'gp', limit: 5 })
  expect(await screen.findByText('GP')).toBeInTheDocument()
})

test('selecting a result navigates to the stock page', async () => {
  ;(api.stocks.list as jest.Mock).mockResolvedValue({
    items: [{ ticker: 'GP', name: 'Grameenphone', sector: 'Telecom', category: 'A', market_cap_bdt: 1, is_active: true }],
    total: 1, limit: 5, offset: 0,
  })
  render(<CommandPalette />)
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
  fireEvent.change(screen.getByLabelText('search'), { target: { value: 'gp' } })
  await act(async () => { jest.advanceTimersByTime(250) })
  fireEvent.click(await screen.findByRole('option'))
  expect(mockPush).toHaveBeenCalledWith('/stocks/GP')
})

test('blank query does not call the API', async () => {
  render(<CommandPalette />)
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
  fireEvent.change(screen.getByLabelText('search'), { target: { value: '   ' } })
  await act(async () => { jest.advanceTimersByTime(250) })
  expect(api.stocks.list).not.toHaveBeenCalled()
})
