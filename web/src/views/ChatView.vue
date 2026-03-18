<script setup lang="ts">
import { ref, computed, onMounted, nextTick, watch, onUnmounted, provide } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import ThemeToggle from '@/components/common/ThemeToggle.vue'
import SessionSidebar from '@/components/chat/SessionSidebar.vue'
import MessageBubble from '@/components/chat/MessageBubble.vue'
import ChatInput from '@/components/chat/ChatInput.vue'
import type { MediaItem } from '@/types'

const auth = useAuthStore()
const chat = useChatStore()

const tokenInput = ref('')
const loginLoading = ref(false)
const loginError = ref('')
const sidebarOpen = ref(false)
const messagesContainer = ref<HTMLElement>()
let statusTimer: ReturnType<typeof setInterval> | undefined

// ── Drawer pin ──
const DRAWER_PIN_KEY = 'astron-drawer-pinned'
const drawerPinned = ref(localStorage.getItem(DRAWER_PIN_KEY) === 'true')
const isMobile = ref(window.innerWidth <= 640)

function onResize() {
  isMobile.value = window.innerWidth <= 640
}

const effectivePinned = computed(() => drawerPinned.value && !isMobile.value)

function handlePin(pinned: boolean) {
  drawerPinned.value = pinned
  localStorage.setItem(DRAWER_PIN_KEY, String(pinned))
  if (pinned) sidebarOpen.value = true
}

// ── Lightbox ──
const lightboxUrl = ref('')
const lightboxOpen = ref(false)

function openLightbox(url: string) {
  lightboxUrl.value = url
  lightboxOpen.value = true
}

function closeLightbox() {
  lightboxOpen.value = false
  lightboxUrl.value = ''
}

function onLightboxKey(e: KeyboardEvent) {
  if (e.key === 'Escape') closeLightbox()
}

provide('openLightbox', openLightbox)

// ── Typing indicator ──
const showTyping = computed(() => {
  if (!chat.streaming) return false
  const msgs = chat.currentMessages
  if (!msgs.length) return false
  const last = msgs[msgs.length - 1]
  return last.role === 'assistant' && !last.content
})

onMounted(() => {
  window.addEventListener('resize', onResize)
  window.addEventListener('keydown', onLightboxKey)

  // Restore pinned sidebar
  if (effectivePinned.value) sidebarOpen.value = true

  const params = new URLSearchParams(location.search)
  const urlToken = params.get('token')
  if (urlToken) {
    tokenInput.value = urlToken
    history.replaceState(null, '', '/')
    handleConnect()
  } else if (auth.isLoggedIn) {
    init()
  }
})

onUnmounted(() => {
  if (statusTimer) clearInterval(statusTimer)
  window.removeEventListener('resize', onResize)
  window.removeEventListener('keydown', onLightboxKey)
})

async function handleConnect() {
  const t = tokenInput.value.trim()
  if (!t) { loginError.value = 'Please enter a token'; return }
  loginLoading.value = true
  loginError.value = ''
  try {
    await auth.loginWithToken(t)
    await init()
  } catch (err) {
    loginError.value = (err as Error).message || 'Connection failed'
  } finally {
    loginLoading.value = false
  }
}

async function init() {
  await chat.loadSessions()
  statusTimer = setInterval(() => auth.checkStatus(), 10000)
}

function disconnect() {
  chat.stopStreaming()
  auth.logout()
  if (statusTimer) { clearInterval(statusTimer); statusTimer = undefined }
}

async function handleSend(content: string, media?: MediaItem[]) {
  await chat.sendMessage(content, media)
  await nextTick()
  scrollToBottom()
}

function scrollToBottom() {
  const el = messagesContainer.value
  if (el) el.scrollTop = el.scrollHeight
}

watch(() => chat.currentMessages.length, () => nextTick(scrollToBottom))
watch(
  () => {
    const msgs = chat.currentMessages
    if (!msgs.length) return ''
    return msgs[msgs.length - 1].content
  },
  () => {
    const el = messagesContainer.value
    if (!el) return
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 120) nextTick(scrollToBottom)
  },
)
</script>

<template>
  <!-- Login screen -->
  <div v-if="!auth.isLoggedIn" class="login-screen">
    <div class="login-card">
      <div class="login-brand">
        <img src="/astron_logo.png" class="login-logo" alt="Logo" />
        <h1>Astron Claw</h1>
        <p>Enter your bot token to start chatting</p>
      </div>
      <div v-if="loginError" class="login-error">{{ loginError }}</div>
      <div class="form-group">
        <label>Bot Token</label>
        <input v-model="tokenInput" type="password" placeholder="sk-..." @keydown.enter="handleConnect" />
      </div>
      <button class="login-btn" :disabled="loginLoading" @click="handleConnect">
        <span v-if="loginLoading" class="spinner"></span>
        {{ loginLoading ? 'Connecting...' : 'Connect' }}
      </button>
    </div>
  </div>

  <!-- Chat screen -->
  <div v-else class="chat-screen" :class="{ 'drawer-pinned': effectivePinned }">
    <SessionSidebar
      v-if="sidebarOpen"
      :pinned="effectivePinned"
      @close="sidebarOpen = false"
      @pin="handlePin(true)"
      @unpin="handlePin(false)"
    />

    <div class="chat-header">
      <button class="icon-btn" @click="sidebarOpen = !sidebarOpen" title="Sessions">&#9776;</button>
      <img src="/astron_logo.png" class="header-logo" alt="Logo" />
      <div class="header-info">
        <span class="header-title">Astron Claw</span>
        <span class="conn-status" :class="auth.botConnected ? 'online' : 'offline'">
          <span class="status-dot"></span>
          {{ auth.botConnected ? 'Bot Connected' : 'Bot Disconnected' }}
        </span>
      </div>
      <div class="header-actions">
        <router-link to="/admin" class="icon-btn" title="Admin">&#9881;</router-link>
        <ThemeToggle />
        <button class="icon-btn disconnect-btn" @click="disconnect" title="Disconnect">&#10005;</button>
      </div>
    </div>

    <!-- Bot status banner -->
    <div class="bot-banner" :class="auth.botConnected ? 'bot-banner-ok' : 'bot-banner-err'">
      {{ auth.botConnected ? 'Bot connected' : 'Bot disconnected \u2014 waiting for bot to connect' }}
    </div>

    <div ref="messagesContainer" class="messages-container">
      <div class="messages-list">
        <template v-if="chat.currentMessages.length">
          <MessageBubble v-for="(msg, i) in chat.currentMessages" :key="i" :message="msg" />
        </template>
        <div v-else class="empty-chat">
          <div class="empty-icon">&#128172;</div>
          <div class="empty-title">Start a conversation</div>
          <div class="empty-desc">Send a message to begin chatting with the bot</div>
        </div>

        <!-- Typing indicator -->
        <div v-if="showTyping" class="typing-indicator">
          <span class="typing-dot"></span>
          <span class="typing-dot"></span>
          <span class="typing-dot"></span>
        </div>
      </div>
    </div>

    <ChatInput @send="handleSend" />

    <!-- Lightbox -->
    <Teleport to="body">
      <div v-if="lightboxOpen" class="lightbox-overlay" @click="closeLightbox">
        <button class="lightbox-close" @click.stop="closeLightbox">&times;</button>
        <img :src="lightboxUrl" class="lightbox-img" @click.stop />
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.login-screen {
  display: flex; align-items: center; justify-content: center;
  min-height: 100vh; padding: 20px;
}
.login-card {
  background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 16px;
  padding: 40px; width: 100%; max-width: 420px; box-shadow: var(--shadow); animation: fadeUp .5s ease;
}
.login-brand { text-align: center; margin-bottom: 32px; }
.login-logo { width: 64px; height: 64px; border-radius: 16px; margin-bottom: 16px; }
.login-brand h1 { font-size: 24px; font-weight: 700; }
.login-brand p { color: var(--text-secondary); font-size: 14px; margin-top: 4px; }
.login-error {
  color: var(--error); font-size: 13px; margin-bottom: 12px;
  padding: 8px 12px; background: rgba(239,68,68,.1); border-radius: var(--radius-sm);
}
.form-group { margin-bottom: 16px; }
.form-group label { display: block; font-size: 13px; font-weight: 600; color: var(--text-secondary); margin-bottom: 6px; }
.form-group input {
  width: 100%; padding: 12px 14px; background: var(--bg-input); border: 1px solid var(--border);
  border-radius: var(--radius-sm); color: var(--text-primary); font-size: 14px;
  font-family: var(--font); outline: none; transition: border-color var(--transition);
}
.form-group input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-dim); }
.form-group input::placeholder { color: var(--text-muted); }
.login-btn {
  width: 100%; display: flex; align-items: center; justify-content: center; gap: 8px;
  padding: 12px 24px; border: none; border-radius: var(--radius-sm);
  font-size: 14px; font-weight: 600; font-family: var(--font); cursor: pointer;
  background: var(--accent); color: #fff; transition: all var(--transition);
}
.login-btn:hover:not(:disabled) { background: var(--accent-hover); transform: translateY(-1px); }
.login-btn:disabled { opacity: .5; cursor: not-allowed; }
.spinner {
  width: 16px; height: 16px; border: 2px solid rgba(255,255,255,.3);
  border-top-color: #fff; border-radius: 50%; animation: spin .6s linear infinite;
}

.chat-screen { display: flex; flex-direction: column; height: 100vh; transition: margin-left 0.2s ease; }
.chat-screen.drawer-pinned { margin-left: 260px; }
.chat-header {
  display: flex; align-items: center; gap: 10px; padding: 12px 16px;
  border-bottom: 1px solid var(--border); background: var(--bg-secondary); flex-shrink: 0;
}
.header-logo { width: 32px; height: 32px; border-radius: 8px; }
.header-info { flex: 1; display: flex; flex-direction: column; gap: 2px; }
.header-title { font-weight: 700; font-size: 15px; }
.conn-status { font-size: 12px; display: flex; align-items: center; gap: 5px; }
.conn-status.online { color: var(--success); }
.conn-status.offline { color: var(--text-muted); }
.conn-status .status-dot {
  width: 6px; height: 6px; border-radius: 50%; background: currentColor;
}
.conn-status.online .status-dot { animation: pulse 2s infinite; }
.header-actions { display: flex; gap: 6px; }
.icon-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 36px; height: 36px; border-radius: var(--radius-sm); background: transparent;
  border: 1px solid var(--border); color: var(--text-secondary); cursor: pointer;
  font-size: 16px; transition: all var(--transition); text-decoration: none;
}
.icon-btn:hover { background: var(--bg-tertiary); color: var(--text-primary); }
.disconnect-btn:hover { color: var(--error); border-color: var(--error); }

/* Bot status banner */
.bot-banner {
  padding: 8px 20px; font-size: 13px; text-align: center; flex-shrink: 0;
  border-bottom: 1px solid var(--border); transition: all 0.3s ease;
}
.bot-banner-ok { background: var(--accent-dim); color: var(--accent); }
.bot-banner-err { background: rgba(239,68,68,.1); color: var(--error); }

.messages-container { flex: 1; overflow-y: auto; padding: 20px; scroll-behavior: smooth; }
.messages-list {
  max-width: 800px; margin: 0 auto;
  display: flex; flex-direction: column; gap: 16px;
}

.empty-chat { text-align: center; padding: 80px 20px; color: var(--text-muted); }
.empty-icon { font-size: 3rem; margin-bottom: 12px; opacity: .4; }
.empty-title { font-size: 1.1rem; margin-bottom: 6px; color: var(--text-secondary); }
.empty-desc { font-size: .85rem; }

/* Typing indicator */
.typing-indicator {
  display: flex; gap: 4px; padding: 4px 0;
}
.typing-dot {
  width: 6px; height: 6px; border-radius: 50%; background: var(--text-muted);
  animation: bounce 1.2s infinite;
}
.typing-dot:nth-child(2) { animation-delay: 0.2s; }
.typing-dot:nth-child(3) { animation-delay: 0.4s; }

/* Lightbox */
.lightbox-overlay {
  position: fixed; inset: 0; z-index: 10000;
  background: rgba(0, 0, 0, 0.85);
  display: flex; align-items: center; justify-content: center;
  cursor: zoom-out;
  animation: fadeIn 0.2s ease;
}
.lightbox-close {
  position: absolute; top: 20px; right: 20px;
  width: 40px; height: 40px;
  display: flex; align-items: center; justify-content: center;
  background: rgba(255, 255, 255, 0.1); color: #fff;
  border: none; border-radius: 50%;
  font-size: 20px; cursor: pointer;
  transition: background 0.2s ease;
}
.lightbox-close:hover { background: rgba(255, 255, 255, 0.2); }
.lightbox-img {
  max-width: 90vw; max-height: 90vh; object-fit: contain;
  border-radius: 8px; cursor: default;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
}
@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

@media (max-width: 640px) {
  .chat-screen.drawer-pinned { margin-left: 0; }
}
</style>
