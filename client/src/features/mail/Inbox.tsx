import type { InboxPage } from './types'

type InboxProps = {
  page: InboxPage
  pageToken: string
  loading: boolean
  error: string
  refreshInbox: () => void
  showOlderMessages: () => void
  onOpenMessage: (id: string) => Promise<void>
}

export default function Inbox({
  page,
  pageToken,
  loading,
  error,
  refreshInbox,
  showOlderMessages,
  onOpenMessage,
}: InboxProps) {
  return (
    <>
      <div className="mail-toolbar">
        <h2>Inbox</h2>
        <button className="secondary" disabled={loading} onClick={refreshInbox}>
          Refresh inbox
        </button>
        <a href="/auth/google/login?reconnect=true">Reconnect Gmail</a>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {loading ? (
        <p role="status">Loading your inbox...</p>
      ) : (
        <>
          {page.messages.length === 0 ? (
            <p>Your inbox is empty.</p>
          ) : (
            <ul className="message-list">
              {page.messages.map((mail) => (
                <li key={mail.id}>
                  <button
                    className={
                      mail.unread ? 'message-row unread' : 'message-row'
                    }
                    onClick={() => void onOpenMessage(mail.id)}
                  >
                    <span className="message-from">
                      {mail.unread && <span className="sr-only">Unread: </span>}
                      {mail.from}
                    </span>
                    <span className="message-subject">
                      {mail.subject || '(No subject)'}
                    </span>
                    <span className="message-snippet">{mail.snippet}</span>
                    <span className="message-date">{mail.date}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {page.nextPageToken && (
            <button className="secondary" onClick={showOlderMessages}>
              Older messages
            </button>
          )}
          {pageToken && (
            <button className="secondary" onClick={refreshInbox}>
              Newest messages
            </button>
          )}
        </>
      )}
    </>
  )
}
