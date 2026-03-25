import client from './client'

export async function uploadFile(
  file: File,
  sessionId?: string,
): Promise<{ url: string; mimeType: string; key?: string }> {
  const formData = new FormData()
  formData.append('file', file)
  if (sessionId) formData.append('sessionId', sessionId)

  const { data } = await client.post('/api/media/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })

  return {
    url: data.downloadUrl ?? data.url,
    mimeType: data.mimeType,
    key: data.key,
  }
}
