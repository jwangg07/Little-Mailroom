import { useEffect, useState } from 'react'
import { getInbox } from './api'
import type { InboxPage } from './types'

export function useInbox() {
  const [page, setPage] = useState<InboxPage>({
    messages: [],
    nextPageToken: null,
  })
  const [pageToken, setPageToken] = useState('')
  const [reload, setReload] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()

    async function loadInbox() {
      setLoading(true)
      setError('')
      try {
        const result = await getInbox(pageToken, controller.signal)
        if (!controller.signal.aborted) setPage(result)
      } catch (reason) {
        if (!controller.signal.aborted) {
          setError(
            reason instanceof Error ? reason.message : 'Could not load inbox.',
          )
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }

    void loadInbox()
    // Paging, refresh, and unmount must not let an older response replace this page.
    return () => controller.abort()
  }, [pageToken, reload])

  function refreshInbox() {
    setPageToken('')
    setReload((value) => value + 1)
  }

  function showOlderMessages() {
    if (page.nextPageToken) setPageToken(page.nextPageToken)
  }

  return {
    page,
    pageToken,
    loading,
    error,
    refreshInbox,
    showOlderMessages,
  }
}
