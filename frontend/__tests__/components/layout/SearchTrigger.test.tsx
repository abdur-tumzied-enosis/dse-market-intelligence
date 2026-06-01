import { render, screen, fireEvent } from '@testing-library/react'
import SearchTrigger from '@/components/layout/SearchTrigger'

test('clicking the trigger dispatches open-command-palette', () => {
  const spy = jest.fn()
  window.addEventListener('open-command-palette', spy)
  render(<SearchTrigger />)
  fireEvent.click(screen.getByRole('button'))
  expect(spy).toHaveBeenCalled()
  window.removeEventListener('open-command-palette', spy)
})
