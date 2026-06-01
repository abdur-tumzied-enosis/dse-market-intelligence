import { render, screen } from '@testing-library/react'
import HeatmapGrid from '../../../components/market/HeatmapGrid'
import type { HeatmapItem } from '@/lib/types'

describe('HeatmapGrid', () => {
  const mockItems: HeatmapItem[] = [
    { ticker: 'ABC', sector: 'Banking', change_pct: 2.5, value_bdt: 100000 },
    { ticker: 'XYZ', sector: 'Telecom', change_pct: 1.2, value_bdt: 50000 },
    { ticker: 'DEF', sector: 'Pharma', change_pct: -1.5, value_bdt: 75000 },
    { ticker: 'GHI', sector: 'Textiles', change_pct: -2.8, value_bdt: 60000 },
    { ticker: 'JKL', sector: 'Energy', change_pct: null, value_bdt: 60000 },
  ]

  it('renders all ticker symbols from the items array', () => {
    render(<HeatmapGrid items={mockItems} />)

    expect(screen.getByText('ABC')).toBeInTheDocument()
    expect(screen.getByText('XYZ')).toBeInTheDocument()
    expect(screen.getByText('DEF')).toBeInTheDocument()
    expect(screen.getByText('GHI')).toBeInTheDocument()
    expect(screen.getByText('JKL')).toBeInTheDocument()
  })

  it('applies inline hex background by change_pct band (positive vs negative)', () => {
    render(<HeatmapGrid items={mockItems} />)

    // ABC change_pct 2.5 falls in the 0..3 band -> #14532d
    const abcCell = screen.getByText('ABC').closest('div')
    expect(abcCell).toHaveStyle({ background: '#14532d' })

    // GHI change_pct -2.8 falls in the -3..0 band -> #7f1d1d
    const ghiCell = screen.getByText('GHI').closest('div')
    expect(ghiCell).toHaveStyle({ background: '#7f1d1d' })
  })

  it('change_pct === null uses the neutral hex background and renders no percentage', () => {
    render(<HeatmapGrid items={mockItems} />)

    const jklCell = screen.getByText('JKL').closest('div')
    expect(jklCell).toHaveStyle({ background: '#1a1a24' })

    // The component renders no percentage text (and no dash) for null change_pct
    expect(screen.queryByText('—')).not.toBeInTheDocument()
    expect(screen.queryByText('NaN%')).not.toBeInTheDocument()
  })

  it('positive change_pct renders with "+" prefix; negative without "+"', () => {
    render(<HeatmapGrid items={mockItems} />)

    expect(screen.getByText('+2.5%')).toBeInTheDocument()
    expect(screen.getByText('+1.2%')).toBeInTheDocument()
    expect(screen.getByText('-1.5%')).toBeInTheDocument()
    expect(screen.getByText('-2.8%')).toBeInTheDocument()
  })
})
