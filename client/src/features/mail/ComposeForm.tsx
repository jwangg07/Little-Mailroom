import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { sendMail } from './api'
import type { MailDraft } from './types'

export default function ComposeForm() {
  const [draft, setDraft] = useState<MailDraft>({
    to: '',
    subject: '',
    body: '',
  })
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  // A ref blocks a second submit before React renders the disabled fieldset.
  const sendingRequest = useRef(false)

  async function sendDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (sendingRequest.current) return
    sendingRequest.current = true
    setSending(true)
    setError('')
    setNotice('')
    try {
      await sendMail(draft)
      setDraft({ to: '', subject: '', body: '' })
      setNotice('Message sent.')
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : 'Send result unknown. Check Sent in Gmail before sending again.',
      )
    } finally {
      sendingRequest.current = false
      setSending(false)
    }
  }

  return (
    <form className="compose" onSubmit={(event) => void sendDraft(event)}>
      <h2>Write a letter</h2>
      <fieldset disabled={sending}>
        <label htmlFor="recipient">To</label>
        <input
          id="recipient"
          type="email"
          required
          value={draft.to}
          onChange={(event) => setDraft({ ...draft, to: event.target.value })}
        />
        <label htmlFor="subject">Subject</label>
        <input
          id="subject"
          maxLength={998}
          value={draft.subject}
          onChange={(event) =>
            setDraft({ ...draft, subject: event.target.value })
          }
        />
        <label htmlFor="body">Message</label>
        <textarea
          id="body"
          required
          rows={8}
          maxLength={100000}
          value={draft.body}
          onChange={(event) => setDraft({ ...draft, body: event.target.value })}
        />
        <button className="google-button" type="submit">
          {sending ? 'Sending...' : 'Send email'}
        </button>
      </fieldset>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
    </form>
  )
}
