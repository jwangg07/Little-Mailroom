export type MailSummary = {
  id: string
  threadId?: string | null
  from: string
  subject: string
  date: string
  snippet: string
  unread: boolean
}

export type MailMessage = {
  id: string
  from: string
  subject: string
  body: string
}

export type InboxPage = {
  messages: MailSummary[]
  nextPageToken: string | null
}

export type MailDraft = {
  to: string
  subject: string
  body: string
}

export type SentMail = { id: string; threadId: string | null }
