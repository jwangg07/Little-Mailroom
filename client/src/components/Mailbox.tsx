import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'

type Mail = { id: string; from: string; subject: string; date: string; snippet: string; unread: boolean }
type Message = { id: string; from: string; subject: string; body: string }

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch('/api/mail' + path, options)
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    throw new Error(typeof data?.detail === 'string' ? data.detail : 'Mail request failed. Please try again.')
  }
  return response.json() as Promise<T>
}

export default function Mailbox() {
  const [page, setPage] = useState<{ messages: Mail[]; nextPageToken: string | null }>({ messages: [], nextPageToken: null })
  const [pageToken, setPageToken] = useState('')
  const [reload, setReload] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<Message | null>(null)
  const [opening, setOpening] = useState(false)
  const [sending, setSending] = useState(false)
  const [notice, setNotice] = useState('')
  const [draft, setDraft] = useState({ to: '', subject: '', body: '' })
  const activeMessage = useRef<AbortController | null>(null)
  const sendingRequest = useRef(false)

  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      setLoading(true)
      setError('')
      try {
        const result = await api<typeof page>('/messages?page_token=' + encodeURIComponent(pageToken), { signal: controller.signal })
        if (!controller.signal.aborted) setPage(result)
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not load inbox.')
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void load()
    return () => controller.abort()
  }, [pageToken, reload])

  useEffect(() => () => activeMessage.current?.abort(), [])

  async function openMessage(id: string) {
    activeMessage.current?.abort()
    const controller = new AbortController()
    activeMessage.current = controller
    setOpening(true)
    setSelected(null)
    setError('')
    try {
      const message = await api<Message>('/messages/' + encodeURIComponent(id), { signal: controller.signal })
      if (!controller.signal.aborted) setSelected(message)
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not open message.')
    } finally {
      if (!controller.signal.aborted) setOpening(false)
    }
  }

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (sendingRequest.current) return
    sendingRequest.current = true
    setSending(true)
    setError('')
    setNotice('')
    try {
      await api('/send', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Mailroom-Request': '1' }, body: JSON.stringify(draft) })
      setDraft({ to: '', subject: '', body: '' })
      setNotice('Message sent.')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Send result unknown. Check Sent in Gmail before sending again.')
    } finally {
      sendingRequest.current = false
      setSending(false)
    }
  }

  return (
    <section className="mailbox" aria-label="Your mail">
      <div className="mail-toolbar">
        <h2>Inbox</h2>
        <button className="secondary" disabled={loading} onClick={() => { setPageToken(''); setReload(value => value + 1) }}>Refresh inbox</button>
        <a href="/auth/google/login?reconnect=true">Reconnect Gmail</a>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {loading ? <p role="status">Loading your inbox...</p> : (
        <>
          {page.messages.length === 0 ? <p>Your inbox is empty.</p> : (
            <ul className="message-list">
              {page.messages.map(mail => (
                <li key={mail.id}>
                  <button className={mail.unread ? 'message-row unread' : 'message-row'} onClick={() => void openMessage(mail.id)}>
                    <span className="message-from">{mail.unread && <span className="sr-only">Unread: </span>}{mail.from}</span>
                    <span className="message-subject">{mail.subject || '(No subject)'}</span>
                    <span className="message-snippet">{mail.snippet}</span>
                    <span className="message-date">{mail.date}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {page.nextPageToken && <button className="secondary" onClick={() => setPageToken(page.nextPageToken ?? '')}>Older messages</button>}
          {pageToken && <button className="secondary" onClick={() => setPageToken('')}>Newest messages</button>}
        </>
      )}
      {opening && <p role="status">Opening message...</p>}
      {selected && (
        <article className="message-detail" aria-label="Open message">
          <button className="secondary" onClick={() => setSelected(null)}>Close message</button>
          <h3>{selected.subject || '(No subject)'}</h3>
          <p>{selected.from}</p>
          <pre>{selected.body}</pre>
        </article>
      )}
      <form className="compose" onSubmit={event => void send(event)}>
        <h2>Write a letter</h2>
        <fieldset disabled={sending}>
          <label htmlFor="recipient">To</label>
          <input id="recipient" type="email" required value={draft.to} onChange={event => setDraft({ ...draft, to: event.target.value })} />
          <label htmlFor="subject">Subject</label>
          <input id="subject" maxLength={998} value={draft.subject} onChange={event => setDraft({ ...draft, subject: event.target.value })} />
          <label htmlFor="body">Message</label>
          <textarea id="body" required rows={8} maxLength={100000} value={draft.body} onChange={event => setDraft({ ...draft, body: event.target.value })} />
          <button className="google-button" type="submit">{sending ? 'Sending...' : 'Send email'}</button>
        </fieldset>
        {notice && <p role="status">{notice}</p>}
      </form>
    </section>
  )
}
