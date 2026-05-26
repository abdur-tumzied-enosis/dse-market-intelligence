import { render, screen } from '@testing-library/react'
import HeatmapGrid from '../../../components/market/HeatmapGrid'
import type { HeatmapItem } from '@/lib/types'

describe('HeatmapGrid', () => {
  const mockItems: HeatmapItem[] = [
    { ticker: 'ABC', sector: 'Banking', change_pct: 2.5, value_bdt: 100000 },
    { ticker: 'XYZ', sector: 'Telecom', change_pct: 1.2, value_bdt: 50000 },
    { ticker: 'DEF', sector: 'Pharma', change_pct: -1.5, value_bdt: 75000 },
    { ticker: 'GHI', sector: 'Textiles', change_pct: -2.8, value_bdt: 60000 },
    { ticker: 'JKL', sector: 'Energy', change_pct: null, value_bdt: null },
  ]

  it('renders all ticker symbols from the items array', () => {
    render(<HeatmapGrid items={mockItems} />)

    expect(screen.getByText('ABC')).toBeInTheDocument()
    expect(screen.getByText('XYZ')).toBeInTheDocument()
    expect(screen.getByText('DEF')).toBeInTheDocument()
    expect(screen.getByText('GHI')).toBeInTheDocument()
    expect(screen.getByText('JKL')).toBeInTheDocument()
  })

  it('change_pct > 2 gets bg-green-700 class; change_pct < -2 gets bg-red-700 class', () => {
    render(<HeatmapGrid items={mockItems} />)

    // ABC has change_pct 2.5 (> 2) should have bg-green-700
    const abcCell = screen.getByText('ABC').closest('div')
    expect(abcCell).toHaveClass('bg-green-700')

    // GHI has change_pct -2.8 (< -2) should have bg-red-700
    const ghiCell = screen.getByText('GHI').closest('div')
    expect(ghiCell).toHaveClass('bg-red-700')
  })

  it('change_pct === null gets bg-surface class and renders "—" instead of a percentage', () => {
    render(<HeatmapGrid items={mockItems} />)

    const jklCell = screen.getByText('JKL').closest('div')
    expect(jklCell).toHaveClass('bg-surface')

    // Find the dash character
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('positive change_pct renders with "+" prefix; negative without "+"', () => {
    render(<HeatmapGrid items={mockItems} />)

    expect(screen.getByText('+2.5%')).toBeInTheDocument()
    expect(screen.getByText('+1.2%')).toBeInTheDocument()
    expect(screen.getByText('-1.5%')).toBeInTheDocument()
    expect(screen.getByText('-2.8%')).toBeInTheDocument()
  })
})
