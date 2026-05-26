import { render, screen, act } from '@testing-library/react'
import MarketStreamBar from '@/components/layout/MarketStreamBar'

type EventSourceListener = (event: MessageEvent) => void

class MockEventSource {
  url: string
  onmessage: EventSourceListener | null = null
  onerror: ((e: Event) => void) | null = null
  static instance: MockEventSource
  static OPEN = 1

  constructor(url: string) {
    this.url = url
    MockEventSource.instance = this
  }
  close() {}
}

beforeAll(() => {
  // @ts-expect-error – mock only
  global.EventSource = MockEventSource
})

test('shows loading state before first event', () => {
  render(<MarketStreamBar apiBase="http://localhost:8000" />)
  expect(screen.getByText('—')).toBeInTheDocument()
})

test('displays DSEX value from SSE event', async () => {
  render(<MarketStreamBar apiBase="http://localhost:8000" />)
  await act(async () => {
    MockEventSource.instance.onmessage?.({
      data: JSON.stringify({ dsex_value: 5330.89, dsex_change_pct: 1.27, market_status: 'Open' }),
    } as MessageEvent)
  })
  expect(screen.getByText('5,330.89')).toBeInTheDocument()
  expect(screen.getByText('+1.27%')).toBeInTheDocument()
})

test('shows error state on SSE error event', async () => {
  render(<MarketStreamBar apiBase="http://localhost:8000" />)
  await act(async () => {
    MockEventSource.instance.onerror?.(new Event('error'))
  })
  expect(screen.getByText('—')).toBeInTheDocument()
})
