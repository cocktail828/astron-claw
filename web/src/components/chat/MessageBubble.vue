<script setup lang="ts">
import { computed, inject } from 'vue'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import ThinkingBlock from './ThinkingBlock.vue'
import ToolCallCard from './ToolCallCard.vue'
import type { ChatMessage, MediaItem } from '@/types'

const props = defineProps<{ message: ChatMessage }>()

const openLightbox = inject<(url: string) => void>('openLightbox', () => {})

// ── MIME / media type detection ──
const EXT_MIME_MAP: Record<string, string> = {
  jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', gif: 'image/gif',
  webp: 'image/webp', svg: 'image/svg+xml', bmp: 'image/bmp', ico: 'image/x-icon',
  mp3: 'audio/mpeg', wav: 'audio/wav', ogg: 'audio/ogg', m4a: 'audio/mp4',
  aac: 'audio/aac', flac: 'audio/flac', wma: 'audio/x-ms-wma',
  mp4: 'video/mp4', webm: 'video/webm', ogv: 'video/ogg', avi: 'video/x-msvideo',
  mov: 'video/quicktime', mkv: 'video/x-matroska',
  pdf: 'application/pdf', zip: 'application/zip', gz: 'application/gzip',
  tar: 'application/x-tar', json: 'application/json', xml: 'application/xml',
}

function guessMime(url: string): string {
  try {
    const pathname = new URL(url, 'http://x').pathname
    const ext = pathname.split('.').pop()?.toLowerCase() || ''
    return EXT_MIME_MAP[ext] || 'application/octet-stream'
  } catch {
    return 'application/octet-stream'
  }
}

function mediaType(item: MediaItem): 'image' | 'audio' | 'video' | 'file' {
  const mime = item.mimeType || guessMime(item.content)
  if (mime.startsWith('image/')) return 'image'
  if (mime.startsWith('audio/')) return 'audio'
  if (mime.startsWith('video/')) return 'video'
  return 'file'
}

function fileName(url: string): string {
  try {
    const pathname = new URL(url, 'http://x').pathname
    return decodeURIComponent(pathname.split('/').pop() || 'file')
  } catch {
    return 'file'
  }
}

// ── Markdown ──
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
const isMultiMedia = computed(() => (props.message.media?.length || 0) > 1)

function onContentClick(e: MouseEvent) {
  const target = e.target as HTMLElement
  if (target.tagName === 'IMG' && target.closest('.markdown-body')) {
    const src = (target as HTMLImageElement).src
    if (src) openLightbox(src)
  }
}
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
      <div v-else class="bubble-text markdown-body" v-html="renderedContent" @click="onContentClick"></div>
    </div>

    <!-- Media attachments -->
    <div v-if="message.media?.length" class="media-list" :class="{ 'media-grid': isMultiMedia }">
      <template v-for="(m, i) in message.media" :key="i">
        <!-- Image -->
        <img
          v-if="mediaType(m) === 'image'"
          :src="m.content"
          class="media-image"
          :class="{ 'media-image-grid': isMultiMedia }"
          @click="openLightbox(m.content)"
          alt="media"
        />
        <!-- Audio -->
        <audio
          v-else-if="mediaType(m) === 'audio'"
          controls
          preload="none"
          class="media-audio"
        >
          <source :src="m.content" :type="m.mimeType || guessMime(m.content)" />
        </audio>
        <!-- Video -->
        <video
          v-else-if="mediaType(m) === 'video'"
          controls
          preload="metadata"
          class="media-video"
        >
          <source :src="m.content" :type="m.mimeType || guessMime(m.content)" />
        </video>
        <!-- File -->
        <a v-else :href="m.content" target="_blank" class="media-file-card" download>
          <span class="media-file-icon">&#128196;</span>
          <span class="media-file-name">{{ fileName(m.content) }}</span>
          <span class="media-file-dl">&#8595;</span>
        </a>
      </template>
    </div>
  </div>
</template>

<style scoped>
.message {
  animation: msgIn 0.3s ease;
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
  word-wrap: break-word;
  overflow-wrap: break-word;
  line-height: 1.6;
  font-size: 14px;
}
.user-bubble {
  background: var(--user-bubble);
  color: var(--user-bubble-text);
  border-bottom-right-radius: 4px;
}
.user-bubble .bubble-text { white-space: pre-wrap; }
.assistant-bubble {
  background: var(--assistant-bubble);
  color: var(--assistant-bubble-text);
  border-bottom-left-radius: 4px;
}

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
.markdown-body :deep(p) { margin: 1em 0; }
.markdown-body :deep(p:first-child) { margin-top: 0; }
.markdown-body :deep(p:last-child) { margin-bottom: 0; }
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
.markdown-body :deep(img:hover) {
  opacity: 0.9;
}

/* Media attachments */
.media-list {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 6px;
}
.media-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.media-image {
  max-width: 300px;
  max-height: 300px;
  border-radius: var(--radius-sm);
  object-fit: cover;
  cursor: pointer;
  transition: opacity 0.2s;
}
.media-image:hover { opacity: 0.9; }
.media-image-grid {
  max-width: 150px;
  max-height: 150px;
}
.media-audio {
  max-width: 300px;
  border-radius: var(--radius-sm);
}
.media-video {
  max-width: 300px;
  max-height: 240px;
  border-radius: var(--radius-sm);
  background: #000;
}
.media-file-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: 13px;
  color: var(--text-primary);
  text-decoration: none;
  transition: background var(--transition);
}
.media-file-card:hover { background: var(--bg-secondary); }
.media-file-icon { font-size: 18px; }
.media-file-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 200px;
}
.media-file-dl {
  color: var(--accent);
  font-size: 16px;
  font-weight: 700;
}

@media (max-width: 640px) {
  .media-image { max-width: 220px; max-height: 220px; }
  .media-image-grid { max-width: 100px; max-height: 100px; }
  .media-video { max-width: 220px; }
}
</style>
