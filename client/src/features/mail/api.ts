import { requestJson } from '../../lib/http'
import type { InboxPage, MailDraft, MailMessage } from './types'

const mailRequestFailed = 'Mail request failed. Please try again.'

export function getInbox(
  pageToken: string,
  signal: AbortSignal,
): Promise<InboxPage> {
  const query = new URLSearchParams({ page_token: pageToken })
  return requestJson(
    `/api/mail/messages?${query}`,
    { signal },
    mailRequestFailed,
  )
}

export function getMessage(
  id: string,
  signal: AbortSignal,
): Promise<MailMessage> {
  return requestJson(
    `/api/mail/messages/${encodeURIComponent(id)}`,
    { signal },
    mailRequestFailed,
  )
}

export async function sendMail(draft: MailDraft): Promise<void> {
  // Never retry a send automatically: a lost response can still mean delivery.
  await requestJson(
    '/api/mail/send',
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Mailroom-Request': '1',
      },
      body: JSON.stringify(draft),
    },
    mailRequestFailed,
  )
}
