import { render, screen } from '@testing-library/react'
import AiAnalysis from '../../../components/stocks/AiAnalysis'
import type { MlPrediction } from '../../../lib/types'

const PRED: MlPrediction = {
  horizon_days: 365,
  predicted_direction: 'UP',
  confidence: 0.82,
  target_price: 145.5,
  predicted_at: '2026-06-01T00:00:00Z',
}

describe('AiAnalysis', () => {
  it('renders a prediction card with horizon, confidence and target', () => {
    render(<AiAnalysis predictions={[PRED]} narrative={null} />)
    expect(screen.getByText('1Y')).toBeInTheDocument()
    expect(screen.getByText('82%')).toBeInTheDocument()
    expect(screen.getByText('৳145.50')).toBeInTheDocument()
  })

  it('renders the empty state when there are no predictions', () => {
    render(<AiAnalysis predictions={[]} narrative={null} />)
    expect(screen.getByText(/no predictions yet/i)).toBeInTheDocument()
  })

  it('renders the narrative when provided', () => {
    render(<AiAnalysis predictions={[]} narrative="Strong fundamentals offset weak momentum." />)
    expect(screen.getByText(/strong fundamentals/i)).toBeInTheDocument()
  })

  it('renders the narrative empty state when absent', () => {
    render(<AiAnalysis predictions={[PRED]} narrative={null} />)
    expect(screen.getByText(/narrative not available yet/i)).toBeInTheDocument()
  })
})
