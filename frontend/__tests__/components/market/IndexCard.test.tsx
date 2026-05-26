import { render, screen } from '@testing-library/react'
import IndexCard from '../../../components/market/IndexCard'

describe('IndexCard', () => {
  it('renders label and formats value with thousands separator and 2 decimals', () => {
    render(<IndexCard label="DSE Index" value={5330.89} changePct={1.5} />)

    expect(screen.getByText('DSE Index')).toBeInTheDocument()
    expect(screen.getByText('5,330.89')).toBeInTheDocument()
  })

  it('renders green class for positive changePct', () => {
    render(<IndexCard label="DSE Index" value={5330.89} changePct={2.5} />)

    const changeElement = screen.getByText('+2.50%')
    expect(changeElement).toHaveClass('text-accent-green')
  })

  it('renders red class for negative changePct', () => {
    render(<IndexCard label="DSE Index" value={5330.89} changePct={-1.75} />)

    const changeElement = screen.getByText('-1.75%')
    expect(changeElement).toHaveClass('text-accent-red')
  })

  it('treats zero changePct as non-negative with green class and + sign', () => {
    render(<IndexCard label="DSE Index" value={5330.89} changePct={0} />)

    const changeElement = screen.getByText('+0.00%')
    expect(changeElement).toHaveClass('text-accent-green')
  })
})
