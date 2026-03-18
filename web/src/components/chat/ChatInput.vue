<script setup lang="ts">
import { ref, computed } from 'vue'
import { uploadFile } from '@/api/media'
import { useChatStore } from '@/stores/chat'
import { useAuthStore } from '@/stores/auth'
import type { MediaItem } from '@/types'

const chat = useChatStore()
const auth = useAuthStore()
const emit = defineEmits<{ send: [content: string, media?: MediaItem[]] }>()

const text = ref('')
const textarea = ref<HTMLTextAreaElement>()
const pendingFiles = ref<{ id: number; file: File; previewUrl?: string }[]>([])
const uploading = ref(false)
let fileIdCounter = 0

const MAX_FILES = 10
const MAX_FILE_SIZE = 500 * 1024 * 1024

const canSend = computed(() => {
  return (text.value.trim() || pendingFiles.value.length > 0) && !chat.streaming && !uploading.value
})

function autoGrow() {
  const el = textarea.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 200) + 'px'
}

function handleKeyDown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

function handlePaste(e: ClipboardEvent) {
  const files = e.clipboardData?.files
  if (files?.length) {
    e.preventDefault()
    addFiles(files)
  }
}

function addFiles(files: FileList | File[]) {
  for (const file of Array.from(files)) {
    if (pendingFiles.value.length >= MAX_FILES) break
    if (file.size === 0 || file.size > MAX_FILE_SIZE) continue
    const previewUrl = file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined
    pendingFiles.value.push({ id: ++fileIdCounter, file, previewUrl })
  }
}

function removeFile(id: number) {
  const idx = pendingFiles.value.findIndex((f) => f.id === id)
  if (idx >= 0) {
    const f = pendingFiles.value[idx]
    if (f.previewUrl) URL.revokeObjectURL(f.previewUrl)
    pendingFiles.value.splice(idx, 1)
  }
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function triggerFileSelect() {
  const input = document.createElement('input')
  input.type = 'file'
  input.multiple = true
  input.onchange = () => {
    if (input.files) addFiles(input.files)
  }
  input.click()
}

async function send() {
  if (!canSend.value) return
  const content = text.value.trim()
  text.value = ''
  if (textarea.value) {
    textarea.value.style.height = 'auto'
  }

  let media: MediaItem[] | undefined

  if (pendingFiles.value.length > 0) {
    uploading.value = true
    try {
      const uploads = await Promise.allSettled(
        pendingFiles.value.map((f) => uploadFile(f.file, chat.currentSessionId || undefined)),
      )
      media = []
      for (const result of uploads) {
        if (result.status === 'fulfilled') {
          media.push({ type: 'url', content: result.value.url, mimeType: result.value.mimeType })
        }
      }
      if (media.length === 0) media = undefined
    } finally {
      // Cleanup
      pendingFiles.value.forEach((f) => { if (f.previewUrl) URL.revokeObjectURL(f.previewUrl) })
      pendingFiles.value = []
      uploading.value = false
    }
  }

  emit('send', content || '', media)
}

// Drag-and-drop
const dragging = ref(false)
let dragCounter = 0

function onDragEnter(e: DragEvent) {
  e.preventDefault()
  dragCounter++
  dragging.value = true
}
function onDragLeave(e: DragEvent) {
  e.preventDefault()
  dragCounter--
  if (dragCounter <= 0) { dragging.value = false; dragCounter = 0 }
}
function onDragOver(e: DragEvent) { e.preventDefault() }
function onDrop(e: DragEvent) {
  e.preventDefault()
  dragging.value = false
  dragCounter = 0
  if (e.dataTransfer?.files.length) {
    addFiles(e.dataTransfer.files)
  }
}
</script>

<template>
  <div
    class="input-area"
    @dragenter="onDragEnter"
    @dragleave="onDragLeave"
    @dragover="onDragOver"
    @drop="onDrop"
  >
    <!-- Drag overlay -->
    <div v-if="dragging" class="drag-overlay">Drop files to attach</div>

    <!-- File preview -->
    <div v-if="pendingFiles.length" class="file-preview">
      <div v-for="f in pendingFiles" :key="f.id" class="file-chip">
        <img v-if="f.previewUrl" :src="f.previewUrl" class="file-thumb" />
        <span v-else class="file-icon">&#128206;</span>
        <span class="file-name">{{ f.file.name }}</span>
        <span class="file-size">{{ formatFileSize(f.file.size) }}</span>
        <button class="file-remove" @click="removeFile(f.id)">&times;</button>
      </div>
    </div>

    <!-- Input row -->
    <div class="input-row">
      <button class="attach-btn" @click="triggerFileSelect" title="Attach files">
        &#128206;
        <span v-if="pendingFiles.length" class="file-badge">{{ pendingFiles.length }}</span>
      </button>
      <textarea
        ref="textarea"
        v-model="text"
        class="msg-input"
        placeholder="Type a message..."
        rows="1"
        @input="autoGrow"
        @keydown="handleKeyDown"
        @paste="handlePaste"
      ></textarea>
      <button
        v-if="chat.streaming"
        class="stop-btn"
        @click="chat.stopStreaming()"
        title="Stop"
      >&#9632;</button>
      <button
        v-else
        class="send-btn"
        :disabled="!canSend"
        @click="send"
        title="Send"
      >&#10148;</button>
    </div>
  </div>
</template>

<style scoped>
.input-area {
  padding: 12px 16px;
  border-top: 1px solid var(--border);
  background: var(--bg-secondary);
  position: relative;
}
.drag-overlay {
  position: absolute;
  inset: 0;
  background: var(--accent-dim);
  border: 2px dashed var(--accent);
  border-radius: var(--radius);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
  color: var(--accent);
  z-index: 10;
}
.file-preview {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.file-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: 12px;
  max-width: 220px;
}
.file-thumb {
  width: 28px;
  height: 28px;
  border-radius: 4px;
  object-fit: cover;
}
.file-icon { font-size: 16px; }
.file-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
}
.file-size { color: var(--text-muted); }
.file-remove {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 16px;
  padding: 0 2px;
  line-height: 1;
}
.file-remove:hover { color: var(--error); }
.file-badge {
  position: absolute;
  top: -4px;
  right: -4px;
  background: var(--accent);
  color: #fff;
  font-size: 10px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
}
.input-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}
.attach-btn {
  position: relative;
  width: 40px;
  height: 40px;
  border-radius: var(--radius-sm);
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all var(--transition);
}
.attach-btn:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.msg-input {
  flex: 1;
  padding: 10px 14px;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font-size: 14px;
  font-family: var(--font);
  resize: none;
  outline: none;
  min-height: 40px;
  max-height: 200px;
  line-height: 1.4;
  transition: border-color var(--transition);
}
.msg-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-dim);
}
.msg-input::placeholder { color: var(--text-muted); }
.send-btn, .stop-btn {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-sm);
  border: none;
  cursor: pointer;
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all var(--transition);
}
.send-btn {
  background: var(--accent);
  color: #fff;
}
.send-btn:hover:not(:disabled) { background: var(--accent-hover); }
.send-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.stop-btn {
  background: var(--error);
  color: #fff;
}
.stop-btn:hover { opacity: 0.85; }
</style>
