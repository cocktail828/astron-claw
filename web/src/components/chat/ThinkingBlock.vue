<script setup lang="ts">
import { ref } from 'vue'

defineProps<{ content: string }>()

const collapsed = ref(true)
</script>

<template>
  <div class="thinking-block" :class="{ collapsed }">
    <div class="thinking-header" @click="collapsed = !collapsed">
      <span class="thinking-icon">&#128161;</span>
      <span class="thinking-label">Thinking</span>
      <span class="thinking-toggle">{{ collapsed ? '&#9654;' : '&#9660;' }}</span>
    </div>
    <div v-show="!collapsed" class="thinking-content">
      <pre>{{ content }}</pre>
    </div>
  </div>
</template>

<style scoped>
.thinking-block {
  background: var(--thinking-bg);
  border: 1px solid var(--thinking-border);
  border-radius: var(--radius-sm);
  margin: 8px 0;
  overflow: hidden;
}
.thinking-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  cursor: pointer;
  font-size: 13px;
  color: var(--text-muted);
  user-select: none;
  transition: background var(--transition);
}
.thinking-header:hover { background: var(--bg-tertiary); }
.thinking-icon { font-size: 14px; }
.thinking-label { flex: 1; font-weight: 600; }
.thinking-toggle { font-size: 10px; }
.thinking-content {
  padding: 12px 14px;
  border-top: 1px solid var(--thinking-border);
}
.thinking-content pre {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
}
</style>
