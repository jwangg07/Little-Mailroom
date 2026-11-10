import { requestJson } from '../../lib/http'
import type { Session } from './types'

export function getSession(signal: AbortSignal): Promise<Session> {
  return requestJson('/auth/me', { signal }, 'Account request failed')
}

export function logout(): Promise<Session> {
  return requestJson(
    '/auth/logout',
    {
      method: 'POST',
      headers: { 'X-Mailroom-Request': '1' },
    },
    'Sign-out failed',
  )
}
