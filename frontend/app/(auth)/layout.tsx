import AuthLayout from '@/components/layout/AuthLayout'

export default function AuthRouteLayout({ children }: { children: React.ReactNode }) {
  return <AuthLayout>{children}</AuthLayout>
}
