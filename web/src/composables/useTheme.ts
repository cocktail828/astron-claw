import { ref, computed } from 'vue'
import { darkTheme } from 'naive-ui'
import type { GlobalTheme } from 'naive-ui'

const STORAGE_KEY = 'astron-theme'

const current = ref<'dark' | 'light'>(
  (localStorage.getItem(STORAGE_KEY) as 'dark' | 'light') || 'dark',
)

// Apply on load
document.documentElement.setAttribute('data-theme', current.value)

export function useTheme() {
  const isDark = computed(() => current.value === 'dark')
  const naiveTheme = computed<GlobalTheme | null>(() =>
    isDark.value ? darkTheme : null,
  )

  function toggle() {
    current.value = current.value === 'dark' ? 'light' : 'dark'
    document.documentElement.setAttribute('data-theme', current.value)
    localStorage.setItem(STORAGE_KEY, current.value)
  }

  return { current, isDark, naiveTheme, toggle }
}
