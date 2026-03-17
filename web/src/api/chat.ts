import client from './client'
import type { SessionListResponse, CreateSessionResponse, MediaItem } from '@/types'

export async function listSessions(): Promise<SessionListResponse> {
  const { data } = await client.get('/bridge/chat/sessions')
  return data
}

export async function createSession(): Promise<CreateSessionResponse> {
  const { data } = await client.post('/bridge/chat/sessions')
  return data
}

/**
 * Send chat message via SSE (POST /bridge/chat).
 * Returns the raw Response for streaming.
 */
export function sendChatMessage(
  content: string,
  sessionId?: string,
  media?: MediaItem[],
): { response: Promise<Response>; abort: AbortController } {
  const abort = new AbortController()
  const token = localStorage.getItem('astron-token') || ''

  const body: Record<string, unknown> = { content }
  if (sessionId) body.sessionId = sessionId
  if (media?.length) body.media = media

  const response = fetch('/bridge/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
    signal: abort.signal,
  })

  return { response, abort }
}
