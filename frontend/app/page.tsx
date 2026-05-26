// frontend/app/page.tsx
import { cookies } from 'next/headers'
import { redirect } from 'next/navigation'
import Link from 'next/link'

export default async function RootPage() {
  const cookieStore = await cookies()
  const token = cookieStore.get('dse_access_token')
  if (token) redirect('/dashboard')

  return (
    <div className="min-h-screen bg-base text-white">
      {/* Nav */}
      <nav className="flex items-center justify-between px-8 py-5 border-b border-border-custom">
        <span className="text-lg font-bold text-white">DSE Intelligence</span>
        <div className="flex gap-3">
          <Link href="/login" className="text-sm text-muted hover:text-white transition-colors px-4 py-2">
            Log in
          </Link>
          <Link href="/register" className="text-sm bg-accent-blue text-white px-4 py-2 rounded-lg hover:opacity-90 transition-opacity">
            Get started
          </Link>
        </div>
      </nav>

      {/* Hero */}
      <section className="max-w-4xl mx-auto px-8 pt-24 pb-16 text-center">
        <h1 className="text-5xl font-bold leading-tight mb-5">
          AI-powered market intelligence<br />
          <span className="text-accent-blue">for Dhaka Stock Exchange</span>
        </h1>
        <p className="text-lg text-muted max-w-xl mx-auto mb-10">
          Real-time DSE market data, ML price predictions, fundamental analysis, and an AI chat analyst — all in one platform.
        </p>
        <div className="flex gap-4 justify-center">
          <Link href="/register" className="bg-accent-blue text-white px-8 py-3 rounded-lg font-semibold hover:opacity-90 transition-opacity">
            Start for free
          </Link>
          <Link href="/login" className="border border-border-custom text-muted px-8 py-3 rounded-lg hover:text-white hover:border-white transition-colors">
            Sign in
          </Link>
        </div>
      </section>

      {/* Features */}
      <section className="max-w-5xl mx-auto px-8 py-16">
        <h2 className="text-2xl font-bold text-center mb-10">Everything you need to invest smarter</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {[
            {
              title: 'Live Market Data',
              desc: 'Real-time DSEX, DS30, DSES indices. Top gainers, losers, and sector heatmap updated every 5 minutes.',
              color: 'text-accent-green',
            },
            {
              title: 'AI Stock Analyst',
              desc: 'Ask anything about DSE stocks in Bengali or English. Powered by Gemini with access to fundamentals and news.',
              color: 'text-accent-blue',
            },
            {
              title: 'ML Predictions',
              desc: '5/10/20 day price direction forecasts with bear/base/bull scenarios using LSTM and XGBoost models.',
              color: 'text-accent-gold',
            },
          ].map((f) => (
            <div key={f.title} className="bg-surface border border-border-custom rounded-xl p-6">
              <h3 className={`text-lg font-semibold mb-3 ${f.color}`}>{f.title}</h3>
              <p className="text-muted text-sm leading-relaxed">{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Pricing */}
      <section className="max-w-3xl mx-auto px-8 py-16">
        <h2 className="text-2xl font-bold text-center mb-10">Simple pricing</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Free */}
          <div className="bg-surface border border-border-custom rounded-xl p-8">
            <p className="text-muted text-sm uppercase tracking-widest mb-2">Free</p>
            <p className="text-4xl font-bold mb-1">৳0</p>
            <p className="text-muted text-sm mb-6">Forever free</p>
            <ul className="space-y-2 text-sm text-muted mb-8">
              {[
                '50 API calls / day',
                '3 AI chat queries / day',
                '3-year fundamentals',
                'Market dashboard',
                'Stock screener',
              ].map((item) => (
                <li key={item} className="flex gap-2">
                  <span className="text-accent-green">✓</span> {item}
                </li>
              ))}
            </ul>
            <Link href="/register" className="block text-center border border-border-custom text-white py-2.5 rounded-lg hover:bg-elevated transition-colors text-sm">
              Get started free
            </Link>
          </div>
          {/* Pro */}
          <div className="bg-surface border border-accent-blue rounded-xl p-8 relative">
            <span className="absolute top-4 right-4 text-xs bg-accent-blue text-white px-2 py-0.5 rounded">Popular</span>
            <p className="text-accent-blue text-sm uppercase tracking-widest mb-2">Pro</p>
            <p className="text-4xl font-bold mb-1">৳999</p>
            <p className="text-muted text-sm mb-6">per month</p>
            <ul className="space-y-2 text-sm text-muted mb-8">
              {[
                '1,000 API calls / day',
                '30 AI chat queries / day',
                '10-year fundamentals',
                'ML price predictions',
                'Unlimited portfolio',
                'PDF reports',
                'Portfolio risk analysis',
              ].map((item) => (
                <li key={item} className="flex gap-2">
                  <span className="text-accent-blue">✓</span> {item}
                </li>
              ))}
            </ul>
            <Link href="/register" className="block text-center bg-accent-blue text-white py-2.5 rounded-lg hover:opacity-90 transition-opacity text-sm font-semibold">
              Start Pro trial
            </Link>
          </div>
        </div>
      </section>

      {/* Disclaimer */}
      <footer className="border-t border-border-custom px-8 py-8 mt-8">
        <p className="text-xs text-muted text-center max-w-3xl mx-auto leading-relaxed">
          <strong className="text-white">Disclaimer:</strong> DSE Stock Intelligence provides market data and analysis tools for informational purposes only. Nothing on this platform constitutes investment advice. Market data may be delayed. Past performance of ML predictions does not guarantee future results. Always consult a qualified financial advisor before making investment decisions. This platform is not affiliated with the Dhaka Stock Exchange (DSE) or any regulatory authority.
        </p>
        <p className="text-xs text-muted text-center mt-4">© 2026 DSE Stock Intelligence. All rights reserved.</p>
      </footer>
    </div>
  )
}
