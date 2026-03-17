<script setup lang="ts">
import { computed } from 'vue'
import { useChatStore } from '@/stores/chat'

const chat = useChatStore()
const emit = defineEmits<{
  close: []
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
</script>

<template>
  <div class="sidebar-overlay" @click="emit('close')"></div>
  <div class="sidebar">
    <div class="sidebar-header">
      <span class="sidebar-title">Sessions</span>
      <button class="new-chat-btn" @click="chat.newSession()">+ New Chat</button>
    </div>
    <div class="session-list">
      <div
        v-for="s in sortedSessions"
        :key="s.id"
        class="session-item"
        :class="{ active: s.id === chat.currentSessionId }"
        @click="chat.switchSession(s.id)"
      >
        <div class="session-name">Session {{ s.number }}</div>
        <div class="session-meta">{{ s.id.slice(0, 8) }}</div>
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
}
.sidebar-title {
  font-weight: 700;
  font-size: 15px;
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
.session-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}
.session-meta {
  font-size: 11px;
  color: var(--text-muted);
  font-family: var(--font-mono);
  margin-top: 2px;
}
.empty-sessions {
  text-align: center;
  padding: 40px 16px;
  color: var(--text-muted);
  font-size: 13px;
}
</style>
