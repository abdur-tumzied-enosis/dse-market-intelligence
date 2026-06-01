import '@testing-library/jest-dom'

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
// jsdom lacks ResizeObserver; provide a no-op stub for tests
const g = globalThis as { ResizeObserver?: typeof ResizeObserver }
g.ResizeObserver = g.ResizeObserver || (ResizeObserverStub as unknown as typeof ResizeObserver)
