<script setup lang="ts">
import { computed, onMounted, nextTick, ref } from 'vue'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import ThinkingBlock from './ThinkingBlock.vue'
import ToolCallCard from './ToolCallCard.vue'
import type { ChatMessage } from '@/types'

const props = defineProps<{ message: ChatMessage }>()
const contentEl = ref<HTMLElement>()

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  highlight(str: string, lang: string) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        const result = hljs.highlight(str, { language: lang })
        return `<div class="code-wrapper"><div class="code-header"><span class="code-lang">${lang}</span><button class="copy-btn" onclick="navigator.clipboard.writeText(this.closest('.code-wrapper').querySelector('code').textContent).then(()=>{this.textContent='Copied!';setTimeout(()=>this.textContent='Copy',1500)})">Copy</button></div><pre><code class="hljs">${result.value}</code></pre></div>`
      } catch { /* ignore */ }
    }
    try {
      const result = hljs.highlightAuto(str)
      return `<div class="code-wrapper"><div class="code-header"><span class="code-lang">code</span><button class="copy-btn" onclick="navigator.clipboard.writeText(this.closest('.code-wrapper').querySelector('code').textContent).then(()=>{this.textContent='Copied!';setTimeout(()=>this.textContent='Copy',1500)})">Copy</button></div><pre><code class="hljs">${result.value}</code></pre></div>`
    } catch { /* ignore */ }
    return ''
  },
})

const renderedContent = computed(() => {
  if (!props.message.content) return ''
  try {
    return md.render(props.message.content)
  } catch {
    return props.message.content
  }
})

const isUser = computed(() => props.message.role === 'user')
const hasToolCalls = computed(() => props.message.toolCalls && props.message.toolCalls.length > 0)
</script>

<template>
  <div class="message" :class="isUser ? 'user-message' : 'assistant-message'">
    <!-- Thinking block -->
    <ThinkingBlock v-if="message.thinking" :content="message.thinking" />

    <!-- Tool calls -->
    <template v-if="hasToolCalls">
      <ToolCallCard v-for="tc in message.toolCalls" :key="tc.id" :tool="tc" />
    </template>

    <!-- Content bubble -->
    <div v-if="message.content" class="bubble" :class="isUser ? 'user-bubble' : 'assistant-bubble'">
      <div v-if="isUser" class="bubble-text">{{ message.content }}</div>
      <div v-else ref="contentEl" class="bubble-text markdown-body" v-html="renderedContent"></div>
    </div>

    <!-- Media attachments (user) -->
    <div v-if="message.media?.length" class="media-list">
      <div v-for="(m, i) in message.media" :key="i" class="media-item">
        <img v-if="m.mimeType?.startsWith('image/')" :src="m.content" class="media-img" />
        <a v-else :href="m.content" target="_blank" class="media-link">📎 Attachment</a>
      </div>
    </div>
  </div>
</template>

<style scoped>
.message {
  animation: msgIn 0.3s ease;
  margin-bottom: 16px;
}
.user-message {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}
.assistant-message {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}
.bubble {
  max-width: 85%;
  padding: 12px 16px;
  border-radius: var(--radius);
  word-break: break-word;
  line-height: 1.6;
  font-size: 14px;
}
.user-bubble {
  background: var(--user-bubble);
  color: var(--user-bubble-text);
  border-bottom-right-radius: 4px;
}
.assistant-bubble {
  background: var(--assistant-bubble);
  color: var(--assistant-bubble-text);
  border-bottom-left-radius: 4px;
}
.bubble-text { white-space: pre-wrap; }

/* Markdown styles */
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  margin: 12px 0 6px;
  font-weight: 600;
}
.markdown-body :deep(h1) { font-size: 1.3em; }
.markdown-body :deep(h2) { font-size: 1.15em; }
.markdown-body :deep(h3) { font-size: 1.05em; }
.markdown-body :deep(p) { margin: 6px 0; }
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 6px 0;
  padding-left: 24px;
}
.markdown-body :deep(li) { margin: 2px 0; }
.markdown-body :deep(blockquote) {
  border-left: 3px solid var(--accent);
  padding: 6px 12px;
  margin: 8px 0;
  color: var(--text-secondary);
  background: var(--bg-tertiary);
  border-radius: 4px;
}
.markdown-body :deep(table) {
  border-collapse: collapse;
  margin: 8px 0;
  font-size: 13px;
  width: 100%;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid var(--border);
  padding: 6px 10px;
  text-align: left;
}
.markdown-body :deep(th) {
  background: var(--bg-tertiary);
  font-weight: 600;
}
.markdown-body :deep(code):not(.hljs) {
  background: var(--bg-tertiary);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 0.88em;
}
.markdown-body :deep(.code-wrapper) {
  margin: 10px 0;
  border-radius: var(--radius-sm);
  overflow: hidden;
  border: 1px solid var(--border);
}
.markdown-body :deep(.code-header) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: var(--bg-tertiary);
  font-size: 12px;
  color: var(--text-muted);
}
.markdown-body :deep(.code-lang) {
  font-weight: 600;
  text-transform: uppercase;
}
.markdown-body :deep(.copy-btn) {
  background: none;
  border: 1px solid var(--border);
  color: var(--text-muted);
  padding: 2px 10px;
  border-radius: 4px;
  font-size: 11px;
  cursor: pointer;
  font-family: var(--font);
}
.markdown-body :deep(.copy-btn:hover) {
  color: var(--accent);
  border-color: var(--accent);
}
.markdown-body :deep(pre) {
  margin: 0;
  padding: 14px;
  overflow-x: auto;
  background: var(--bg-primary);
  font-size: 13px;
  line-height: 1.5;
}
.markdown-body :deep(a) {
  color: var(--accent);
}
.markdown-body :deep(img) {
  max-width: 100%;
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.media-list {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 6px;
}
.media-img {
  max-width: 200px;
  max-height: 200px;
  border-radius: var(--radius-sm);
  object-fit: cover;
  cursor: pointer;
}
.media-link {
  padding: 6px 12px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
  font-size: 13px;
}
</style>
