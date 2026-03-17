<script setup lang="ts">
import { ref, computed } from 'vue'
import type { ToolCall } from '@/types'

const props = defineProps<{ tool: ToolCall }>()
const collapsed = ref(true)

const TOOL_DISPLAY: Record<string, { label: string; icon: string }> = {
  read: { label: 'Read File', icon: '\u{1F4C4}' },
  exec: { label: 'Execute Command', icon: '\u25B6\uFE0F' },
  write: { label: 'Write File', icon: '\u{1F4DD}' },
  edit: { label: 'Edit File', icon: '\u2702\uFE0F' },
  memory_search: { label: 'Search Memory', icon: '\u{1F9E0}' },
  web_search: { label: 'Web Search', icon: '\u{1F50D}' },
  bash: { label: 'Run Shell', icon: '\u{1F4BB}' },
  python: { label: 'Run Python', icon: '\u{1F40D}' },
  search: { label: 'Search', icon: '\u{1F50E}' },
  list: { label: 'List Files', icon: '\u{1F4C1}' },
  delete: { label: 'Delete', icon: '\u{1F5D1}\uFE0F' },
}

const display = computed(() => {
  const name = props.tool.name.toLowerCase()
  if (TOOL_DISPLAY[name]) return TOOL_DISPLAY[name]
  const label = name.replace(/[_-]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
  return { label, icon: '\u2699\uFE0F' }
})

const statusClass = computed(() => `status-${props.tool.status}`)
const truncatedResult = computed(() => {
  if (!props.tool.result) return ''
  return props.tool.result.length > 5000
    ? props.tool.result.slice(0, 5000) + '\n... (truncated)'
    : props.tool.result
})
</script>

<template>
  <div class="tool-card" :class="[statusClass, { collapsed }]">
    <div class="tool-header" @click="collapsed = !collapsed">
      <span class="tool-stripe"></span>
      <span class="tool-icon">{{ display.icon }}</span>
      <span class="tool-name">{{ display.label }}</span>
      <span v-if="tool.status === 'running'" class="tool-spinner"></span>
      <span v-else-if="tool.status === 'completed'" class="tool-check">&#10003;</span>
      <span v-else-if="tool.status === 'error'" class="tool-error-icon">&#10007;</span>
      <span class="tool-toggle">{{ collapsed ? '&#9654;' : '&#9660;' }}</span>
    </div>
    <div v-show="!collapsed" class="tool-body">
      <div v-if="tool.arguments" class="tool-section">
        <div class="tool-section-label">Arguments</div>
        <pre class="tool-pre">{{ tool.arguments }}</pre>
      </div>
      <div v-if="truncatedResult" class="tool-section">
        <div class="tool-section-label">Result</div>
        <pre class="tool-pre">{{ truncatedResult }}</pre>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tool-card {
  border: 1px solid var(--tool-border);
  border-radius: var(--radius-sm);
  margin: 8px 0;
  overflow: hidden;
  background: var(--tool-bg);
  border-left: 3px solid var(--tool-accent);
  transition: border-color var(--transition);
}
.tool-card.status-completed { border-left-color: var(--success); }
.tool-card.status-error { border-left-color: var(--error); }
.tool-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  cursor: pointer;
  font-size: 13px;
  color: var(--text-secondary);
  user-select: none;
  transition: background var(--transition);
}
.tool-header:hover { background: var(--tool-running-dim); }
.tool-icon { font-size: 14px; }
.tool-name { flex: 1; font-weight: 600; color: var(--text-primary); }
.tool-toggle { font-size: 10px; color: var(--text-muted); }
.tool-spinner {
  width: 14px; height: 14px;
  border: 2px solid var(--tool-accent-dim);
  border-top-color: var(--tool-accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
.tool-check { color: var(--success); font-weight: 700; }
.tool-error-icon { color: var(--error); font-weight: 700; }
.tool-body {
  border-top: 1px solid var(--tool-border);
  padding: 12px 14px;
}
.tool-section { margin-bottom: 10px; }
.tool-section:last-child { margin-bottom: 0; }
.tool-section-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 6px;
}
.tool-pre {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.5;
  max-height: 300px;
  overflow-y: auto;
  background: var(--bg-tertiary);
  padding: 10px;
  border-radius: 6px;
}
.tool-stripe { display: none; }
</style>
