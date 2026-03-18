import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as adminApi from '@/api/admin'
import type { Token, AuthStatusResponse } from '@/types'

export const useAdminStore = defineStore('admin', () => {
  const authenticated = ref(false)
  const needSetup = ref(false)
  const loading = ref(false)

  // Token list state
  const tokens = ref<Token[]>([])
  const totalTokens = ref(0)
  const onlineBots = ref(0)
  const activeChats = ref(0)
  const totalCount = ref(0)
  const page = ref(1)
  const pageSize = ref(20)
  const search = ref('')
  const sortBy = ref('created_at')
  const sortOrder = ref<'asc' | 'desc'>('desc')
  const botStatus = ref('')

  async function checkAuth() {
    loading.value = true
    try {
      const status: AuthStatusResponse = await adminApi.getAuthStatus()
      needSetup.value = status.need_setup
      authenticated.value = status.authenticated
    } finally {
      loading.value = false
    }
  }

  async function setup(password: string) {
    await adminApi.setupPassword(password)
    authenticated.value = true
    needSetup.value = false
  }

  async function login(password: string) {
    await adminApi.login(password)
    authenticated.value = true
  }

  async function logout() {
    await adminApi.logout()
    authenticated.value = false
  }

  async function fetchTokens() {
    loading.value = true
    try {
      const data = await adminApi.listTokens({
        page: page.value,
        page_size: pageSize.value,
        search: search.value,
        sort_by: sortBy.value,
        sort_order: sortOrder.value,
        bot_status: botStatus.value,
      })
      tokens.value = data.tokens
      totalCount.value = data.total
      onlineBots.value = data.online_bots
      totalTokens.value = data.total_tokens
      activeChats.value = data.active_chats ?? 0
    } finally {
      loading.value = false
    }
  }

  async function createToken(name: string, expiresIn: number) {
    const token = await adminApi.adminCreateToken(name, expiresIn)
    await fetchTokens()
    return token
  }

  async function deleteToken(token: string) {
    await adminApi.adminDeleteToken(token)
    await fetchTokens()
  }

  async function updateToken(token: string, updates: { name?: string; expires_in?: number }) {
    await adminApi.adminUpdateToken(token, updates)
    await fetchTokens()
  }

  async function cleanup() {
    const result = await adminApi.adminCleanup()
    await fetchTokens()
    return result
  }

  return {
    authenticated,
    needSetup,
    loading,
    tokens,
    totalTokens,
    onlineBots,
    activeChats,
    totalCount,
    page,
    pageSize,
    search,
    sortBy,
    sortOrder,
    botStatus,
    checkAuth,
    setup,
    login,
    logout,
    fetchTokens,
    createToken,
    deleteToken,
    updateToken,
    cleanup,
  }
})
