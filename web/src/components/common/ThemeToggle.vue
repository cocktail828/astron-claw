<script setup lang="ts">
import { useTheme } from '@/composables/useTheme'
const { isDark, toggle } = useTheme()
</script>

<template>
  <button class="icon-btn" @click="toggle" title="Toggle theme">
    <span v-if="isDark">&#9790;</span>
    <span v-else>&#9788;</span>
  </button>
</template>

<style scoped>
.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: var(--radius-sm);
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 16px;
  transition: all var(--transition);
}
.icon-btn:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
  border-color: var(--text-muted);
}
</style>
