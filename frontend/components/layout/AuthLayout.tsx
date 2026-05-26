export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-base p-4">
      <div className="w-full max-w-md bg-surface rounded-lg border border-border p-8">
        {children}
      </div>
    </div>
  )
}
