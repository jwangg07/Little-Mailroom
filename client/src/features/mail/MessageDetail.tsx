import type { MailMessage } from './types'

type MessageDetailProps = {
  message: MailMessage
  onClose: () => void
}

export default function MessageDetail({
  message,
  onClose,
}: MessageDetailProps) {
  return (
    <article className="message-detail" aria-label="Open message">
      <button className="secondary" onClick={onClose}>
        Close message
      </button>
      <h3>{message.subject || '(No subject)'}</h3>
      <p>{message.from}</p>
      <pre>{message.body}</pre>
    </article>
  )
}
