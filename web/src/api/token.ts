import client from './client'

export async function createToken(): Promise<string> {
  const { data } = await client.post('/api/token')
  return data.token
}

export async function validateToken(token: string): Promise<{ valid: boolean; bot_connected: boolean }> {
  const { data } = await client.post('/api/token/validate', { token })
  return { valid: data.valid, bot_connected: data.bot_connected }
}
