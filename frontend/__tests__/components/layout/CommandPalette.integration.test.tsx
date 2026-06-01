import { render, screen, fireEvent } from '@testing-library/react'

// Only next/navigation and the API are mocked. @/components/ui/command and
// @/components/ui/dialog are the REAL modules so this test exercises the actual
// cmdk <Command> root context + Base-UI Dialog wiring — guarding against the
// "CommandPrimitive must be used within a Command" crash on palette open.
jest.mock('next/navigation', () => ({ useRouter: () => ({ push: jest.fn() }) }))
jest.mock('@/lib/api', () => ({
  api: {
    stocks: {
      list: jest.fn().mockResolvedValue({ items: [], total: 0, limit: 5, offset: 0 }),
    },
  },
}))

beforeAll(() => {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (q: string) => ({
      matches: false,
      media: q,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  })
  Element.prototype.scrollIntoView = () => {}
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }
})

import CommandPalette from '@/components/layout/CommandPalette'

test('palette opens with real cmdk/dialog wiring (no missing-context crash)', () => {
  render(<CommandPalette />)
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
  // The real CommandInput renders an input with our placeholder — its presence
  // proves the cmdk <Command> context mounted without throwing.
  expect(
    screen.getByPlaceholderText('Search stocks by ticker or name…'),
  ).toBeInTheDocument()
})
