import client from './client'

export async function fetchMetricsRaw(): Promise<string> {
  const { data } = await client.get('/api/metrics', {
    responseType: 'text',
    transformResponse: [(data) => data],
  })
  return data
}

export async function resetMetrics(adminSession: string): Promise<void> {
  await client.delete('/api/metrics', {
    headers: { Authorization: `Bearer ${adminSession}` },
  })
}
