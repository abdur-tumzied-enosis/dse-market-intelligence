import { render, screen } from '@testing-library/react'
import MoverStrip from '../../../components/market/MoverStrip'
import type { TopMover } from '@/lib/types'

describe('MoverStrip', () => {
  const mockMovers: TopMover[] = [
    { ticker: 'ABC', name: 'ABC Company', close: 125.5, change_pct: 2.5 },
    { ticker: 'XYZ', name: 'XYZ Limited', close: 89.25, change_pct: 1.2 },
  ]

  it('renders title and all ticker symbols from the movers array', () => {
    render(<MoverStrip title="Top Gainers" movers={mockMovers} variant="gain" />)

    expect(screen.getByText('Top Gainers')).toBeInTheDocument()
    expect(screen.getByText('ABC')).toBeInTheDocument()
    expect(screen.getByText('XYZ')).toBeInTheDocument()
  })

  it('variant="gain" applies text-accent-green class to change values and prefixes +', () => {
    render(<MoverStrip title="Top Gainers" movers={mockMovers} variant="gain" />)

    const firstChange = screen.getByText('+2.50%')
    const secondChange = screen.getByText('+1.20%')
    expect(firstChange).toHaveClass('text-accent-green')
    expect(secondChange).toHaveClass('text-accent-green')
  })

  it('variant="loss" applies text-accent-red class to change values (no + prefix)', () => {
    const losersMovers: TopMover[] = [
      { ticker: 'DEF', name: 'DEF Corp', close: 45.0, change_pct: -1.5 },
      { ticker: 'GHI', name: 'GHI Industries', close: 78.5, change_pct: -2.75 },
    ]
    render(<MoverStrip title="Top Losers" movers={losersMovers} variant="loss" />)

    const firstChange = screen.getByText('-1.50%')
    const secondChange = screen.getByText('-2.75%')
    expect(firstChange).toHaveClass('text-accent-red')
    expect(secondChange).toHaveClass('text-accent-red')
  })

  it('empty movers array renders title but no list items', () => {
    render(<MoverStrip title="Top Gainers" movers={[]} variant="gain" />)

    expect(screen.getByText('Top Gainers')).toBeInTheDocument()
    const listItems = screen.queryAllByRole('listitem')
    expect(listItems).toHaveLength(0)
  })
})
