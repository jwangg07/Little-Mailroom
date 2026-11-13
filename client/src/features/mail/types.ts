export type MailSummary = {
  id: string
  from: string
  subject: string
  date: string
  snippet: string
  unread: boolean
}

export type MailMessage = {
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
