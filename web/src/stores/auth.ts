import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { createToken, validateToken } from '@/api/token'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('astron-token') || '')
  const botConnected = ref(false)
  const loading = ref(false)

  const isLoggedIn = computed(() => !!token.value)

  async function generate() {
    loading.value = true
    try {
      const t = await createToken()
      token.value = t
      localStorage.setItem('astron-token', t)
      await checkStatus()
    } finally {
      loading.value = false
    }
  }

  async function loginWithToken(t: string) {
    loading.value = true
    try {
      const result = await validateToken(t)
      if (!result.valid) throw new Error('Invalid token')
      token.value = t
      botConnected.value = result.bot_connected
      localStorage.setItem('astron-token', t)
    } finally {
      loading.value = false
    }
  }

  async function checkStatus() {
    if (!token.value) return
    try {
      const result = await validateToken(token.value)
      if (!result.valid) {
        logout()
        return
      }
      botConnected.value = result.bot_connected
    } catch {
      // Network error — keep token
    }
  }

  function logout() {
    token.value = ''
    botConnected.value = false
    localStorage.removeItem('astron-token')
  }

  return {
    token,
    botConnected,
    loading,
    isLoggedIn,
    generate,
    loginWithToken,
    checkStatus,
    logout,
  }
})
