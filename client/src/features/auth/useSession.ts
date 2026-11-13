import { useEffect, useRef, useState } from 'react'
import { getSession, logout } from './api'
import type { User } from './types'

const authErrors: Record<string, string> = {
  access_denied:
    'Google sign-in was canceled. You can try again whenever you’re ready.',
  invalid_state:
    'This sign-in attempt expired or could not be verified. Please try again.',
  not_configured:
    'Add your Google client ID and secret to server/.env, then restart the backend.',
  failed: 'Google sign-in could not be completed. Please try again.',
}

export function useSession() {
  const signingOutRequest = useRef(false)
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [signingOut, setSigningOut] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(() => {
    const code = new URLSearchParams(window.location.search).get('auth_error')
    return code ? (authErrors[code] ?? authErrors.failed) : null
  })

  useEffect(() => {
    const controller = new AbortController()
    const url = new URL(window.location.href)
    if (url.searchParams.has('auth_error')) {
      url.searchParams.delete('auth_error')
      window.history.replaceState(null, '', url)
    }
    async function loadAccount() {
      try {
        const data = await getSession(controller.signal)
        if (!controller.signal.aborted) setUser(data.user)
      } catch {
        if (!controller.signal.aborted) {
          setError(
            'Could not reach the mailroom. Check that the backend is running, then refresh.',
          )
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void loadAccount()
    // StrictMode cleanup and unmount must prevent stale account updates.
    return () => controller.abort()
  }, [])

  async function signOut() {
    if (signingOutRequest.current) return
    signingOutRequest.current = true
    setSigningOut(true)
    setError(null)
    setNotice(null)
    try {
      const result = await logout()
      setUser(null)
      setNotice(result.revoked
        ? 'Signed out on all devices. Gmail is disconnected and saved access was removed. Your emails remain in Gmail.'
        : 'Signed out of this browser. We could not confirm Gmail disconnection because your session or saved access expired. Remove Mini Mailroom in your Google Account connections to revoke any remaining permission.')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Sign-out could not be confirmed. Please try again.')
    } finally {
      signingOutRequest.current = false
      setSigningOut(false)
    }
  }
  return { user, loading, signingOut, notice, error, signOut }
}
