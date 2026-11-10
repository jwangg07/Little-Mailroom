/** Shared JSON requests. Callers own cancellation and user-facing fallback text. */
export async function requestJson<T>(
  path: string,
  options: RequestInit | undefined,
  fallbackMessage: string,
): Promise<T> {
  const response = await fetch(path, options)
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    throw new Error(
      typeof data?.detail === 'string' ? data.detail : fallbackMessage,
    )
  }
  return response.json() as Promise<T>
}
