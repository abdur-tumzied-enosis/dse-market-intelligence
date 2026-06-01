import { render, screen } from '@testing-library/react'
import SectorHeatmap from '@/components/sectors/SectorHeatmap'
import type { SectorRow } from '@/lib/types'

const rows: SectorRow[] = [
  { sector: 'Telecom', pe: 15, change_pct: 2.1, market_cap_bdt: 5000, fetched_at: null },
  { sector: 'Bank', pe: 9, change_pct: -1.4, market_cap_bdt: 8000, fetched_at: null },
]

describe('SectorHeatmap', () => {
  it('renders a tile labelled for each sector', () => {
    render(<SectorHeatmap sectors={rows} />)
    expect(screen.getByText('Telecom')).toBeInTheDocument()
    expect(screen.getByText('Bank')).toBeInTheDocument()
  })
  it('shows the change percent on tiles', () => {
    render(<SectorHeatmap sectors={rows} />)
    expect(screen.getByText('+2.1%')).toBeInTheDocument()
    expect(screen.getByText('-1.4%')).toBeInTheDocument()
  })
})
