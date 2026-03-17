import client from './client'
import type { AuthStatusResponse, TokenListResponse } from '@/types'

// ── Auth ─────────────────────────────────────────
export async function getAuthStatus(): Promise<AuthStatusResponse> {
  const { data } = await client.get('/api/admin/auth/status')
  return data
}

export async function setupPassword(password: string): Promise<void> {
  await client.post('/api/admin/auth/setup', { password })
}

export async function login(password: string): Promise<void> {
  await client.post('/api/admin/auth/login', { password })
}

export async function logout(): Promise<void> {
  await client.post('/api/admin/auth/logout')
}

// ── Token CRUD ───────────────────────────────────
export async function listTokens(params: {
  page?: number
  page_size?: number
  search?: string
  sort_by?: string
  sort_order?: string
  bot_status?: string
}): Promise<TokenListResponse> {
  const { data } = await client.get('/api/admin/tokens', { params })
  return data
}

export async function adminCreateToken(name: string, expires_in: number): Promise<string> {
  const { data } = await client.post('/api/admin/tokens', { name, expires_in })
  return data.token
}

export async function adminDeleteToken(token: string): Promise<void> {
  await client.delete(`/api/admin/tokens/${token}`)
}

export async function adminUpdateToken(
  token: string,
  updates: { name?: string; expires_in?: number },
): Promise<void> {
  await client.patch(`/api/admin/tokens/${token}`, updates)
}

export async function adminCleanup(): Promise<{ removed_tokens: number; removed_sessions: number }> {
  const { data } = await client.post('/api/admin/cleanup')
  return data
}
