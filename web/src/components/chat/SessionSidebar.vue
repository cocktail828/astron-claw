<script setup lang="ts">
import { computed } from 'vue'
import { useChatStore } from '@/stores/chat'

const chat = useChatStore()
const props = defineProps<{ pinned: boolean }>()
const emit = defineEmits<{
  close: []
  pin: []
  unpin: []
}>()

const sortedSessions = computed(() =>
  [...chat.sessions].reverse(),
)

function relativeTime(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime()
  if (diff < 60000) return 'now'
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h`
  return `${Math.floor(diff / 86400000)}d`
}

function sessionPreview(sessionId: string): string {
  const msgs = chat.messages[sessionId]
  if (!msgs || !msgs.length) return 'No messages yet'
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === 'user' && msgs[i].content) {
      const text = msgs[i].content
      return text.length > 50 ? text.slice(0, 50) + '...' : text
    }
  }
  return 'No messages yet'
}

function sessionTime(sessionId: string): string {
  const msgs = chat.messages[sessionId]
  if (!msgs || !msgs.length) return ''
  const last = msgs[msgs.length - 1]
  if (!last.timestamp) return ''
  return relativeTime(new Date(last.timestamp).toISOString())
}
</script>

<template>
  <div v-if="!pinned" class="sidebar-overlay" @click="emit('close')"></div>
  <div class="sidebar" :class="{ 'sidebar-pinned': pinned }">
    <div class="sidebar-header">
      <span class="sidebar-title">Sessions</span>
      <div class="sidebar-header-actions">
        <button
          class="sidebar-icon-btn"
          :class="{ active: pinned }"
          :title="pinned ? 'Unpin sidebar' : 'Pin sidebar'"
          @click="pinned ? emit('unpin') : emit('pin')"
        >&#128204;</button>
        <button class="sidebar-icon-btn" title="Close" @click="emit('close')">&#10005;</button>
        <button class="new-chat-btn" @click="chat.newSession()">+ New Chat</button>
      </div>
    </div>
    <div class="session-list">
      <div
        v-for="s in sortedSessions"
        :key="s.id"
        class="session-item"
        :class="{ active: s.id === chat.currentSessionId }"
        @click="chat.switchSession(s.id)"
      >
        <div class="session-top">
          <span class="session-icon">&#128172;</span>
          <span class="session-name">Session {{ s.number }}</span>
          <span class="session-time">{{ sessionTime(s.id) }}</span>
        </div>
        <div class="session-preview">{{ sessionPreview(s.id) }}</div>
      </div>
      <div v-if="!sortedSessions.length" class="empty-sessions">
        No sessions yet
      </div>
    </div>
  </div>
</template>

<style scoped>
.sidebar-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: 99;
}
.sidebar {
  position: fixed;
  left: 0;
  top: 0;
  bottom: 0;
  width: 260px;
  background: var(--bg-secondary);
  border-right: 1px solid var(--border);
  z-index: 100;
  display: flex;
  flex-direction: column;
  animation: slideIn 0.2s ease;
}
.sidebar-pinned {
  animation: none;
}
@keyframes slideIn {
  from { transform: translateX(-100%); }
  to { transform: translateX(0); }
}
.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px;
  border-bottom: 1px solid var(--border);
  gap: 8px;
}
.sidebar-title {
  font-weight: 700;
  font-size: 15px;
  flex-shrink: 0;
}
.sidebar-header-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}
.sidebar-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-muted);
  cursor: pointer;
  font-size: 12px;
  transition: all var(--transition);
}
.sidebar-icon-btn:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}
.sidebar-icon-btn.active {
  background: var(--accent-dim);
  color: var(--accent);
  border-color: var(--accent);
}
.new-chat-btn {
  padding: 6px 14px;
  border-radius: var(--radius-sm);
  background: var(--accent);
  color: #fff;
  border: none;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  font-family: var(--font);
  transition: background var(--transition);
  white-space: nowrap;
}
.new-chat-btn:hover { background: var(--accent-hover); }
.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}
.session-item {
  padding: 12px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  margin-bottom: 4px;
  transition: background var(--transition);
}
.session-item:hover { background: var(--bg-tertiary); }
.session-item.active {
  background: var(--accent-dim);
  border-left: 3px solid var(--accent);
}
.session-top {
  display: flex;
  align-items: center;
  gap: 6px;
}
.session-icon {
  font-size: 12px;
  flex-shrink: 0;
}
.session-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.session-time {
  font-size: 11px;
  color: var(--text-muted);
  flex-shrink: 0;
}
.session-preview {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding-left: 18px;
}
.empty-sessions {
  text-align: center;
  padding: 40px 16px;
  color: var(--text-muted);
  font-size: 13px;
}
</style>
