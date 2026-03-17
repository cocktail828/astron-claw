import axios from 'axios'

const client = axios.create({
  baseURL: '',
  timeout: 30000,
  withCredentials: true,
})

// Inject Bearer token for bridge/media endpoints
client.interceptors.request.use((config) => {
  const token = localStorage.getItem('astron-token')
  if (token && config.url) {
    const needsAuth =
      config.url.startsWith('/bridge/') || config.url.startsWith('/api/media/')
    if (needsAuth) {
      config.headers.Authorization = `Bearer ${token}`
    }
  }
  return config
})

// Unified error handling
client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response) {
      const data = error.response.data
      if (data?.error) {
        return Promise.reject(new Error(data.error))
      }
    }
    return Promise.reject(error)
  },
)

export default client
