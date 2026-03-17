<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { NModal, NButton, NInput, NSelect, NDataTable, NPagination, NSpace, NTag, NPopconfirm, useMessage } from 'naive-ui'
import { useAdminStore } from '@/stores/admin'
import AppHeader from '@/components/common/AppHeader.vue'
import type { Token } from '@/types'

const admin = useAdminStore()
const message = useMessage()

// Auth form state
const password = ref('')
const password2 = ref('')
const authError = ref('')
const authLoading = ref(false)

// Create modal
const showCreateModal = ref(false)
const createName = ref('')
const createExpiry = ref(86400)
const createLoading = ref(false)

// Token result modal
const showResultModal = ref(false)
const resultToken = ref('')

// Edit modal
const showEditModal = ref(false)
const editTarget = ref('')
const editName = ref('')
const editExpiry = ref(-1)
const editLoading = ref(false)

// Refresh timer
let refreshTimer: ReturnType<typeof setInterval> | undefined
const REFRESH_INTERVAL = 5000

const expiryOptions = [
  { label: '1 Hour', value: 3600 },
  { label: '6 Hours', value: 21600 },
  { label: '1 Day', value: 86400 },
  { label: '7 Days', value: 604800 },
  { label: '30 Days', value: 2592000 },
  { label: 'Never', value: 0 },
]

const editExpiryOptions = [
  { label: 'Keep Current', value: -1 },
  ...expiryOptions,
]

onMounted(async () => {
  await admin.checkAuth()
  if (admin.authenticated) startRefresh()
})

onUnmounted(() => stopRefresh())

function startRefresh() {
  admin.fetchTokens()
  refreshTimer = setInterval(() => admin.fetchTokens(), REFRESH_INTERVAL)
}
function stopRefresh() {
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = undefined }
}

// Auth handlers
async function handleSetup() {
  authError.value = ''
  if (password.value.length < 4) { authError.value = 'Password must be at least 4 characters'; return }
  if (password.value !== password2.value) { authError.value = 'Passwords do not match'; return }
  authLoading.value = true
  try {
    await admin.setup(password.value)
    startRefresh()
  } catch (e) { authError.value = (e as Error).message }
  finally { authLoading.value = false; password.value = ''; password2.value = '' }
}

async function handleLogin() {
  authError.value = ''
  authLoading.value = true
  try {
    await admin.login(password.value)
    startRefresh()
  } catch (e) { authError.value = (e as Error).message }
  finally { authLoading.value = false; password.value = '' }
}

async function handleLogout() {
  stopRefresh()
  await admin.logout()
}

// Token CRUD
async function handleCreate() {
  createLoading.value = true
  try {
    const token = await admin.createToken(createName.value, createExpiry.value)
    showCreateModal.value = false
    resultToken.value = token
    showResultModal.value = true
    createName.value = ''
    createExpiry.value = 86400
  } catch (e) { message.error((e as Error).message) }
  finally { createLoading.value = false }
}

function openEdit(t: Token) {
  editTarget.value = t.token
  editName.value = t.name
  editExpiry.value = -1
  showEditModal.value = true
}

async function handleEdit() {
  editLoading.value = true
  try {
    const updates: { name?: string; expires_in?: number } = { name: editName.value }
    if (editExpiry.value !== -1) updates.expires_in = editExpiry.value
    await admin.updateToken(editTarget.value, updates)
    showEditModal.value = false
    message.success('Token updated')
  } catch (e) { message.error((e as Error).message) }
  finally { editLoading.value = false }
}

async function handleDelete(token: string) {
  try {
    await admin.deleteToken(token)
    message.success('Token deleted')
  } catch (e) { message.error((e as Error).message) }
}

async function handleCleanup() {
  try {
    const r = await admin.cleanup()
    message.success(`Cleaned ${r.removed_tokens} tokens, ${r.removed_sessions} sessions`)
  } catch (e) { message.error((e as Error).message) }
}

function copyToken(token: string) {
  navigator.clipboard.writeText(token)
  message.success('Copied to clipboard')
}

function maskToken(t: string): string {
  if (t.length <= 12) return t
  return t.slice(0, 6) + '...' + t.slice(-4)
}

function formatTime(ts: string): string {
  return new Date(ts).toLocaleString()
}

function formatRelativeExpiry(ts: string): string {
  const diff = new Date(ts).getTime() - Date.now()
  if (diff < 0) return 'Expired'
  if (diff > 365 * 86400000) return 'Never'
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h`
  return `${Math.floor(diff / 86400000)}d`
}

// Search debounce
let searchTimer: ReturnType<typeof setTimeout> | undefined
function handleSearch(val: string) {
  admin.search = val
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => { admin.page = 1; admin.fetchTokens() }, 300)
}

// Table columns
const columns = computed(() => [
  { title: '#', key: 'index', width: 50, render: (_: Token, idx: number) => (admin.page - 1) * admin.pageSize + idx + 1 },
  { title: 'Name', key: 'name', ellipsis: { tooltip: true } },
  {
    title: 'Token', key: 'token', width: 180,
    render: (row: Token) => {
      return h('span', {
        style: 'font-family:var(--font-mono);font-size:12px;cursor:pointer',
        onClick: () => copyToken(row.token),
        title: 'Click to copy',
      }, maskToken(row.token))
    },
  },
  { title: 'Created', key: 'created_at', width: 170, render: (row: Token) => formatTime(row.created_at) },
  { title: 'Expires', key: 'expires_at', width: 100, render: (row: Token) => formatRelativeExpiry(row.expires_at) },
  {
    title: 'Bot', key: 'bot_online', width: 80,
    render: (row: Token) => h(NTag, { type: row.bot_online ? 'success' : 'default', size: 'small', bordered: false }, () => row.bot_online ? 'Online' : 'Offline'),
  },
  {
    title: 'Actions', key: 'actions', width: 200,
    render: (row: Token) => h(NSpace, { size: 'small' }, () => [
      h('a', { href: `/?token=${encodeURIComponent(row.token)}`, style: 'font-size:12px;color:var(--accent)' }, 'Chat'),
      h(NButton, { size: 'tiny', quaternary: true, onClick: () => openEdit(row) }, () => 'Edit'),
      h(NPopconfirm, { onPositiveClick: () => handleDelete(row.token) }, {
        trigger: () => h(NButton, { size: 'tiny', quaternary: true, type: 'error' }, () => 'Delete'),
        default: () => 'Delete this token?',
      }),
    ]),
  },
])

// Need h import for render functions
import { h } from 'vue'
</script>

<template>
  <!-- Setup screen -->
  <div v-if="admin.needSetup" class="auth-screen">
    <div class="auth-card">
      <div class="auth-brand">
        <img src="/astron_logo.png" class="auth-logo" alt="Logo" />
        <h1>Astron Claw</h1>
        <p>Set up admin password</p>
      </div>
      <div v-if="authError" class="auth-error">{{ authError }}</div>
      <div class="form-group">
        <label>Password</label>
        <input v-model="password" type="password" placeholder="At least 4 characters" @keydown.enter="handleSetup" />
      </div>
      <div class="form-group">
        <label>Confirm Password</label>
        <input v-model="password2" type="password" placeholder="Confirm password" @keydown.enter="handleSetup" />
      </div>
      <button class="auth-btn" :disabled="authLoading" @click="handleSetup">
        {{ authLoading ? 'Setting up...' : 'Set Password' }}
      </button>
    </div>
  </div>

  <!-- Login screen -->
  <div v-else-if="!admin.authenticated" class="auth-screen">
    <div class="auth-card">
      <div class="auth-brand">
        <img src="/astron_logo.png" class="auth-logo" alt="Logo" />
        <h1>Astron Claw</h1>
        <p>Admin Login</p>
      </div>
      <div v-if="authError" class="auth-error">{{ authError }}</div>
      <div class="form-group">
        <label>Password</label>
        <input v-model="password" type="password" placeholder="Enter admin password" @keydown.enter="handleLogin" />
      </div>
      <button class="auth-btn" :disabled="authLoading" @click="handleLogin">
        {{ authLoading ? 'Logging in...' : 'Login' }}
      </button>
    </div>
  </div>

  <!-- Main admin panel -->
  <div v-else class="page">
    <AppHeader title="Astron Claw" subtitle="Admin">
      <router-link to="/" class="icon-btn" title="Chat">&#8962;</router-link>
      <router-link to="/metrics" class="icon-btn" title="Metrics">&#128202;</router-link>
      <button class="icon-btn logout-btn" @click="handleLogout">Logout</button>
    </AppHeader>

    <!-- Stats -->
    <div class="stats">
      <div class="stat-card">
        <div class="label">Total Tokens</div>
        <div class="value accent">{{ admin.totalTokens }}</div>
      </div>
      <div class="stat-card">
        <div class="label">Online Bots</div>
        <div class="value success">{{ admin.onlineBots }}</div>
      </div>
      <div class="stat-card">
        <div class="label">Showing</div>
        <div class="value">{{ admin.totalCount }}</div>
      </div>
    </div>

    <!-- Toolbar -->
    <div class="toolbar">
      <NButton type="primary" @click="showCreateModal = true">+ New Token</NButton>
      <NButton @click="handleCleanup">Cleanup Expired</NButton>
      <NButton
        :type="admin.botStatus === 'online' ? 'success' : 'default'"
        @click="admin.botStatus = admin.botStatus === 'online' ? '' : 'online'; admin.page = 1; admin.fetchTokens()"
      >
        {{ admin.botStatus === 'online' ? '&#10003; Online' : 'Online' }}
      </NButton>
      <div style="flex:1"></div>
      <NInput
        :value="admin.search"
        placeholder="Search tokens..."
        clearable
        style="width: 220px"
        @update:value="handleSearch"
      />
    </div>

    <!-- Table -->
    <NDataTable
      :columns="columns"
      :data="admin.tokens"
      :loading="admin.loading"
      :bordered="false"
      size="small"
      :row-key="(row: Token) => row.token"
    />

    <div style="margin-top: 16px; display: flex; justify-content: flex-end">
      <NPagination
        v-model:page="admin.page"
        :page-size="admin.pageSize"
        :item-count="admin.totalCount"
        :on-update:page="() => admin.fetchTokens()"
        show-size-picker
        :page-sizes="[10, 20, 50]"
        :on-update:page-size="(s: number) => { admin.pageSize = s; admin.page = 1; admin.fetchTokens() }"
      />
    </div>

    <!-- Create Token Modal -->
    <NModal v-model:show="showCreateModal" preset="card" title="Create Token" style="max-width: 440px">
      <div class="form-group">
        <label>Name (optional)</label>
        <NInput v-model:value="createName" placeholder="e.g. My Bot" />
      </div>
      <div class="form-group">
        <label>Expiry</label>
        <NSelect v-model:value="createExpiry" :options="expiryOptions" />
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showCreateModal = false">Cancel</NButton>
          <NButton type="primary" :loading="createLoading" @click="handleCreate">Create</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- Token Result Modal -->
    <NModal v-model:show="showResultModal" preset="card" title="Token Created" style="max-width: 440px">
      <p style="margin-bottom:12px;color:var(--text-secondary);font-size:13px">Copy this token — it won't be shown again in full.</p>
      <div class="token-result" @click="copyToken(resultToken)">
        <code>{{ resultToken }}</code>
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="copyToken(resultToken)">Copy</NButton>
          <NButton type="primary" @click="showResultModal = false">Done</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- Edit Token Modal -->
    <NModal v-model:show="showEditModal" preset="card" title="Edit Token" style="max-width: 440px">
      <div class="form-group">
        <label>Name</label>
        <NInput v-model:value="editName" placeholder="Token name" />
      </div>
      <div class="form-group">
        <label>Expiry</label>
        <NSelect v-model:value="editExpiry" :options="editExpiryOptions" />
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showEditModal = false">Cancel</NButton>
          <NButton type="primary" :loading="editLoading" @click="handleEdit">Save</NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.auth-screen {
  display: flex; align-items: center; justify-content: center;
  min-height: 100vh; padding: 20px;
}
.auth-card {
  background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 16px;
  padding: 40px; width: 100%; max-width: 420px; box-shadow: var(--shadow); animation: fadeUp .5s ease;
}
.auth-brand { text-align: center; margin-bottom: 32px; }
.auth-logo { width: 64px; height: 64px; border-radius: 16px; margin-bottom: 16px; }
.auth-brand h1 { font-size: 24px; font-weight: 700; }
.auth-brand p { color: var(--text-secondary); font-size: 14px; margin-top: 4px; }
.auth-error {
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
.auth-btn {
  width: 100%; display: flex; align-items: center; justify-content: center; gap: 8px;
  padding: 12px 24px; border: none; border-radius: var(--radius-sm);
  font-size: 14px; font-weight: 600; font-family: var(--font); cursor: pointer;
  background: var(--accent); color: #fff; transition: all var(--transition);
}
.auth-btn:hover:not(:disabled) { background: var(--accent-hover); }
.auth-btn:disabled { opacity: .5; cursor: not-allowed; }

.page { max-width: 1000px; margin: 0 auto; padding: 24px 20px; }
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 24px; }
.stat-card {
  background: var(--bg-secondary); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 20px; box-shadow: var(--shadow);
}
.stat-card .label { font-size: 13px; color: var(--text-muted); margin-bottom: 6px; }
.stat-card .value { font-size: 28px; font-weight: 700; }
.stat-card .value.accent { color: var(--accent); }
.stat-card .value.success { color: var(--success); }

.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; }

.icon-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 36px; height: 36px; border-radius: var(--radius-sm); background: transparent;
  border: 1px solid var(--border); color: var(--text-secondary); cursor: pointer;
  font-size: 16px; transition: all var(--transition); text-decoration: none;
}
.icon-btn:hover { background: var(--bg-tertiary); color: var(--text-primary); }
.logout-btn { width: auto; padding: 0 12px; font-size: 12px; font-family: var(--font); font-weight: 600; }
.logout-btn:hover { color: var(--error); border-color: var(--error); }

.token-result {
  padding: 14px; background: var(--bg-tertiary); border: 1px solid var(--border);
  border-radius: var(--radius-sm); cursor: pointer; word-break: break-all;
  font-family: var(--font-mono); font-size: 13px; transition: background var(--transition);
}
.token-result:hover { background: var(--accent-dim); }

@media (max-width: 640px) {
  .stats { grid-template-columns: 1fr; }
}
</style>
