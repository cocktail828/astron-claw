import { defineStore } from 'pinia'
import { ref, computed, triggerRef } from 'vue'
import * as chatApi from '@/api/chat'
import { consumeSSE } from '@/composables/useSSE'
import type { ChatMessage, ChatSession, ToolCall, SSEEvent, MediaItem } from '@/types'

export const useChatStore = defineStore('chat', () => {
  const sessions = ref<ChatSession[]>([])
  const currentSessionId = ref<string | null>(null)
  // Use Record instead of Map for reliable Vue reactivity
  const messages = ref<Record<string, ChatMessage[]>>({})
  const streaming = ref(false)
  const abortController = ref<AbortController | null>(null)

  const currentMessages = computed(() =>
    currentSessionId.value ? messages.value[currentSessionId.value] || [] : [],
  )

  async function loadSessions() {
    try {
      const data = await chatApi.listSessions()
      sessions.value = data.sessions
      if (sessions.value.length && !currentSessionId.value) {
        currentSessionId.value = sessions.value[0].id
      }
    } catch {
      // Ignore
    }
  }

  async function newSession() {
    const data = await chatApi.createSession()
    sessions.value = data.sessions
    currentSessionId.value = data.sessionId
    return data.sessionId
  }

  function switchSession(sessionId: string) {
    currentSessionId.value = sessionId
  }

  async function sendMessage(content: string, media?: MediaItem[]) {
    if (streaming.value) return

    // Ensure we have a session
    let sid = currentSessionId.value
    if (!sid) {
      sid = await newSession()
    }

    if (!messages.value[sid!]) {
      messages.value[sid!] = []
    }

    // Add user message
    messages.value[sid!].push({
      role: 'user',
      content,
      timestamp: Date.now(),
      media,
    })

    // Add assistant message placeholder
    messages.value[sid!].push({
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      toolCalls: [],
      thinking: '',
    })

    // Track the index so we always access through the reactive chain
    const msgIndex = messages.value[sid!].length - 1
    const sessionId = sid!

    streaming.value = true
    const { response, abort } = chatApi.sendChatMessage(content, sessionId, media)
    abortController.value = abort

    try {
      const res = await response

      await consumeSSE(
        res,
        (event: SSEEvent) => {
          // Access through reactive chain — NOT a stale local reference
          const msg = messages.value[sessionId]?.[msgIndex]
          if (msg) {
            handleSSEEvent(event, msg, sessionId)
            // Force Vue to detect nested mutations
            triggerRef(messages)
          }
        },
        (error: Error) => {
          const msg = messages.value[sessionId]?.[msgIndex]
          if (msg) {
            msg.content += `\n\n**Error:** ${error.message}`
            triggerRef(messages)
          }
        },
      )
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        const msg = messages.value[sessionId]?.[msgIndex]
        if (msg) {
          msg.content += `\n\n**Error:** ${(err as Error).message}`
          triggerRef(messages)
        }
      }
    } finally {
      streaming.value = false
      abortController.value = null
    }
  }

  function handleSSEEvent(event: SSEEvent, msg: ChatMessage, sessionId: string) {
    const { data } = event

    switch (event.event) {
      case 'session':
        if (data.sessionId && data.sessionId !== sessionId) {
          currentSessionId.value = data.sessionId as string
        }
        break

      case 'chunk':
        msg.content += (data.content as string) || ''
        break

      case 'thinking':
        msg.thinking = (msg.thinking || '') + ((data.content as string) || '')
        break

      case 'tool_call': {
        const tc: ToolCall = {
          id: (data.id as string) || String(Date.now()),
          name: (data.name as string) || '',
          arguments: (data.arguments as string) || '',
          status: 'running',
        }
        if (!msg.toolCalls) msg.toolCalls = []
        msg.toolCalls.push(tc)
        break
      }

      case 'tool_result': {
        const id = data.id as string
        const tc = msg.toolCalls?.find((t) => t.id === id)
        if (tc) {
          tc.result = (data.content as string) || ''
          tc.status = (data.is_error as boolean) ? 'error' : 'completed'
        }
        break
      }

      case 'media': {
        const mediaContent = (data.content as string) || (data.url as string) || ''
        if (mediaContent) {
          msg.content += `\n![media](${mediaContent})\n`
        }
        break
      }

      case 'error':
        msg.content += `\n\n**Error:** ${(data.content as string) || 'Unknown error'}`
        break

      case 'done':
        if (data.content && !msg.content) {
          msg.content = data.content as string
        }
        break
    }
  }

  function stopStreaming() {
    abortController.value?.abort()
    streaming.value = false
  }

  function clearHistory(sessionId?: string) {
    const sid = sessionId || currentSessionId.value
    if (sid) {
      messages.value[sid] = []
    }
  }

  return {
    sessions,
    currentSessionId,
    messages,
    streaming,
    currentMessages,
    loadSessions,
    newSession,
    switchSession,
    sendMessage,
    stopStreaming,
    clearHistory,
  }
})
