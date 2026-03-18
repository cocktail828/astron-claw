<script setup lang="ts">
import { NConfigProvider, NMessageProvider, NDialogProvider } from 'naive-ui'
import { useTheme } from '@/composables/useTheme'

const { naiveTheme } = useTheme()
</script>

<template>
  <NConfigProvider :theme="naiveTheme">
    <NMessageProvider>
      <NDialogProvider>
        <router-view />
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
</template>
