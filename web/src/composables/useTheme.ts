import { ref, computed } from 'vue'
import { darkTheme } from 'naive-ui'
import type { GlobalTheme } from 'naive-ui'

const STORAGE_KEY = 'astron-theme'

const current = ref<'dark' | 'light'>(
  (localStorage.getItem(STORAGE_KEY) as 'dark' | 'light') || 'dark',
)

// Apply on load
document.documentElement.setAttribute('data-theme', current.value)

// ── highlight.js theme management ──
const HLJS_DARK = 'https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/styles/github-dark.min.css'
const HLJS_LIGHT = 'https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/styles/github.min.css'
const HLJS_LINK_ID = 'hljs-theme-link'

function applyHljsTheme(theme: 'dark' | 'light') {
  let link = document.getElementById(HLJS_LINK_ID) as HTMLLinkElement | null
  if (!link) {
    link = document.createElement('link')
    link.id = HLJS_LINK_ID
    link.rel = 'stylesheet'
    document.head.appendChild(link)
  }
  link.href = theme === 'dark' ? HLJS_DARK : HLJS_LIGHT
}

// Apply hljs theme on load
applyHljsTheme(current.value)

export function useTheme() {
  const isDark = computed(() => current.value === 'dark')
  const naiveTheme = computed<GlobalTheme | null>(() =>
    isDark.value ? darkTheme : null,
  )

  function toggle() {
    current.value = current.value === 'dark' ? 'light' : 'dark'
    document.documentElement.setAttribute('data-theme', current.value)
    localStorage.setItem(STORAGE_KEY, current.value)
    applyHljsTheme(current.value)
  }

  return { current, isDark, naiveTheme, toggle }
}
