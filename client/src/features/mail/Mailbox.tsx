import { useEffect, useRef, useState } from 'react'
import { getMessage } from './api'
import ComposeForm from './ComposeForm'
import Inbox from './Inbox'
import MessageDetail from './MessageDetail'
import type { MailMessage } from './types'
import { useInbox } from './useInbox'
import './mail.css'

export default function Mailbox() {
  const inbox = useInbox()
  const [selected, setSelected] = useState<MailMessage | null>(null)
  const [opening, setOpening] = useState(false)
  const [error, setError] = useState('')
  const activeMessage = useRef<AbortController | null>(null)

  useEffect(() => () => activeMessage.current?.abort(), [])

  async function openMessage(id: string) {
    activeMessage.current?.abort()
    const controller = new AbortController()
    activeMessage.current = controller
    setOpening(true)
    setSelected(null)
    setError('')
    try {
      const message = await getMessage(id, controller.signal)
      if (!controller.signal.aborted) setSelected(message)
    } catch (reason) {
      if (!controller.signal.aborted)
        setError(
          reason instanceof Error ? reason.message : 'Could not open message.',
        )
    } finally {
      if (!controller.signal.aborted) setOpening(false)
    }
  }

  function closeMessage() {
    setSelected(null)
  }

  return (
    <section className="mailbox" aria-label="Your mail">
      <Inbox {...inbox} onOpenMessage={openMessage} />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {opening && <p role="status">Opening message...</p>}
      {selected && <MessageDetail message={selected} onClose={closeMessage} />}
      <ComposeForm />
    </section>
  )
}
