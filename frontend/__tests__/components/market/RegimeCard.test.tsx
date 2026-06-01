import { render, screen } from '@testing-library/react'
import RegimeCard from '../../../components/market/RegimeCard'
import type { MarketRegime } from '../../../lib/types'

const base: MarketRegime = {
  regime: 'Bull', dsex: 5330.89, ma: 5200.0, window: 50,
  provisional: false, distance_pct: 2.52, as_of: '2026-06-01', data_status: 'ok',
}

describe('RegimeCard', () => {
  it('renders Bull with green accent and positive distance', () => {
    render(<RegimeCard data={base} />)
    const regime = screen.getByText('Bull')
    expect(regime).toBeInTheDocument()
    expect(regime).toHaveClass('text-accent-green')
    expect(screen.getByText(/\+2\.52%/)).toBeInTheDocument()
  })

  it('renders Bear with red accent', () => {
    render(<RegimeCard data={{ ...base, regime: 'Bear', dsex: 5000, ma: 5200, distance_pct: -3.85 }} />)
    const regime = screen.getByText('Bear')
    expect(regime).toHaveClass('text-accent-red')
  })

  it('shows provisional window label when provisional', () => {
    render(<RegimeCard data={{ ...base, provisional: true, window: 32, data_status: 'provisional' }} />)
    expect(screen.getByText('provisional (32/50d)')).toBeInTheDocument()
  })

  it('shows collecting-data state when insufficient', () => {
    render(<RegimeCard data={{ ...base, regime: 'Unknown', ma: null, window: 3, data_status: 'insufficient', distance_pct: null }} />)
    expect(screen.getByText('Collecting data')).toBeInTheDocument()
  })
})
