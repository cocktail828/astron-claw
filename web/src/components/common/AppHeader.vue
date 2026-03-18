<script setup lang="ts">
import ThemeToggle from './ThemeToggle.vue'

defineProps<{
  title: string
  subtitle?: string
}>()
</script>

<template>
  <div class="header">
    <img src="/astron_logo.png" class="logo-sm" alt="Logo">
    <h1>{{ title }} <small v-if="subtitle">{{ subtitle }}</small></h1>
    <div class="header-actions">
      <slot />
      <ThemeToggle />
    </div>
  </div>
</template>

<style scoped>
.header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 28px;
  flex-wrap: wrap;
}
.header h1 {
  font-size: 20px;
  font-weight: 700;
  flex: 1;
}
.header h1 small {
  font-weight: 400;
  color: var(--text-muted);
  font-size: 14px;
  margin-left: 8px;
}
.logo-sm {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  object-fit: cover;
  flex-shrink: 0;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
