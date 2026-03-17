import type { SSEEvent, SSEEventType } from '@/types'

/**
 * Parse a raw SSE text stream from a fetch Response.
 * Calls `onEvent` for each parsed event.
 */
export async function consumeSSE(
  response: Response,
  onEvent: (event: SSEEvent) => void,
  onError?: (error: Error) => void,
): Promise<void> {
  if (!response.ok) {
    // Try to read error body
    try {
      const body = await response.json()
      onError?.(new Error(body.error || `HTTP ${response.status}`))
    } catch {
      onError?.(new Error(`HTTP ${response.status}`))
    }
    return
  }

  const reader = response.body?.getReader()
  if (!reader) {
    onError?.(new Error('No response body'))
    return
  }

  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent = ''
  let currentData = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          currentData = line.slice(6)
        } else if (line === '') {
          // Empty line = end of event
          if (currentEvent && currentData) {
            try {
              const data = JSON.parse(currentData)
              onEvent({
                event: currentEvent as SSEEventType,
                data,
              })
            } catch {
              // Skip malformed JSON
            }
          }
          currentEvent = ''
          currentData = ''
        }
        // Skip heartbeat comments (lines starting with ':')
      }
    }
  } catch (err) {
    if ((err as Error).name !== 'AbortError') {
      onError?.(err as Error)
    }
  }
}
