import { render, screen } from '@testing-library/react'
import KeyMetricsStrip from '../../../components/stocks/KeyMetricsStrip'

describe('KeyMetricsStrip', () => {
  const groups = [
    { title: 'Valuation', rows: [{ label: 'P/E Ratio', value: '12.5' }] },
    { title: 'Per Share', rows: [{ label: 'EPS', value: '৳4.20' }] },
  ]

  it('renders group titles and metric rows', () => {
    render(<KeyMetricsStrip groups={groups} score={68} />)
    expect(screen.getByText('Valuation')).toBeInTheDocument()
    expect(screen.getByText('P/E Ratio')).toBeInTheDocument()
    expect(screen.getByText('12.5')).toBeInTheDocument()
    expect(screen.getByText('EPS')).toBeInTheDocument()
  })

  it('renders without crashing when score is null', () => {
    render(<KeyMetricsStrip groups={groups} score={null} />)
    expect(screen.getByText('Valuation')).toBeInTheDocument()
  })
})
