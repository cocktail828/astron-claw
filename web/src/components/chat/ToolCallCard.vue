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
const statusText = computed(() => {
  switch (props.tool.status) {
    case 'running': return 'Running...'
    case 'completed': return 'Completed'
    case 'error': return 'Error'
    default: return ''
  }
})

const formattedArgs = computed(() => {
  if (!props.tool.arguments) return ''
  try {
    const parsed = JSON.parse(props.tool.arguments)
    return JSON.stringify(parsed, null, 2)
  } catch {
    return props.tool.arguments
  }
})

const formattedResult = computed(() => {
  if (!props.tool.result) return ''
  let text = props.tool.result.length > 5000
    ? props.tool.result.slice(0, 5000) + '\n... (truncated)'
    : props.tool.result
  try {
    const parsed = JSON.parse(text)
    return JSON.stringify(parsed, null, 2)
  } catch {
    return text
  }
})
</script>

<template>
  <div class="tool-card" :class="[statusClass, { collapsed }]">
    <div class="tool-card-header" @click="collapsed = !collapsed">
      <div class="tool-card-icon">
        <div v-if="tool.status === 'running'" class="tool-card-spinner"></div>
        <span v-else-if="tool.status === 'completed'" class="status-icon">&#10003;</span>
        <span v-else-if="tool.status === 'error'" class="status-icon">&#10007;</span>
      </div>
      <div class="tool-card-info">
        <div class="tool-card-name">{{ display.icon }} {{ display.label }}</div>
        <div class="tool-card-status">{{ statusText }}</div>
      </div>
      <span class="tool-card-chevron">&#9660;</span>
    </div>
    <div class="tool-card-body">
      <div v-if="formattedArgs" class="tool-card-section">
        <div class="tool-card-section-label">Input</div>
        <div class="tool-card-section-content">{{ formattedArgs }}</div>
      </div>
      <div v-if="formattedResult" class="tool-card-section tool-output">
        <div class="tool-card-section-label">Output</div>
        <div class="tool-card-section-content">{{ formattedResult }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
@keyframes toolSpin {
  to { transform: rotate(360deg); }
}
@keyframes toolPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.tool-card {
  max-width: 85%;
  border: 1px solid var(--tool-border);
  border-left: 2px solid var(--tool-border);
  background: var(--tool-bg);
  border-radius: var(--radius-sm);
  overflow: hidden;
  transition: border-color 0.3s ease;
}
.tool-card.status-running { border-left-color: var(--tool-stripe-running); }
.tool-card.status-completed { border-left-color: var(--tool-stripe-completed); }
.tool-card.status-error { border-left-color: var(--tool-stripe-error); }

.tool-card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  cursor: pointer;
  user-select: none;
  transition: background 0.15s ease;
}
.tool-card-header:hover { background: var(--tool-running-dim); }

.tool-card-icon {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 15px;
}
.tool-card.status-running .tool-card-icon { background: var(--tool-accent-dim); }
.tool-card.status-completed .tool-card-icon { background: var(--tool-success-dim); }
.tool-card.status-error .tool-card-icon { background: var(--tool-error-dim); }

.tool-card-spinner {
  width: 16px;
  height: 16px;
  border: 2px solid var(--tool-accent-dim);
  border-top-color: var(--tool-accent);
  border-radius: 50%;
  animation: toolSpin 0.8s linear infinite;
}

.status-icon {
  font-size: 14px;
  font-weight: 700;
}
.tool-card.status-completed .status-icon { color: var(--success); }
.tool-card.status-error .status-icon { color: var(--error); }

.tool-card-info {
  flex: 1;
  min-width: 0;
}

.tool-card-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.3;
}

.tool-card-status {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 1px;
}
.tool-card.status-running .tool-card-status {
  color: var(--tool-accent);
  animation: toolPulse 1.5s ease-in-out infinite;
}
.tool-card.status-completed .tool-card-status { color: var(--success); }
.tool-card.status-error .tool-card-status { color: var(--error); }

.tool-card-chevron {
  font-size: 10px;
  color: var(--text-muted);
  transition: transform 0.2s ease;
  flex-shrink: 0;
}
.tool-card.collapsed .tool-card-chevron { transform: rotate(-90deg); }

.tool-card-body {
  max-height: 600px;
  opacity: 1;
  overflow: hidden;
  transition: max-height 0.3s ease, opacity 0.2s ease;
  border-top: 1px solid var(--tool-border);
}
.tool-card.collapsed .tool-card-body {
  max-height: 0;
  opacity: 0;
  border-top-color: transparent;
}

.tool-card-section {
  padding: 8px 14px 10px;
}
.tool-card-section + .tool-card-section {
  border-top: 1px dashed var(--tool-border);
}

.tool-card-section-label {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  margin-bottom: 4px;
}

.tool-card-section-content {
  font-size: 13px;
  font-family: var(--font-mono);
  color: var(--text-secondary);
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 300px;
  overflow-y: auto;
}
.tool-card.status-error .tool-card-section.tool-output .tool-card-section-content {
  color: var(--error);
}
</style>
