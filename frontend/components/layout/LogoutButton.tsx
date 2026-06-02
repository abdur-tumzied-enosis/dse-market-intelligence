'use client'

import { useRouter } from 'next/navigation'
import { Button } from '@/components/ui/button'
import { clearTokens } from '@/lib/auth'

export default function LogoutButton() {
  const router = useRouter()

  function handleLogout() {
    clearTokens()
    router.push('/login')
  }

  return (
    <Button variant="ghost" size="sm" onClick={handleLogout}>
      Log out
    </Button>
  )
}
