<script setup lang="ts">
import type { ToolCall } from '../api/types'
import { toolLabel } from '../lib/chatState'

defineProps<{ tools: ToolCall[] }>()

function brief(t: ToolCall): string {
  const a = t.args as Record<string, unknown>
  const q = a.query ?? a.sql ?? a.share_code ?? a.fund_code
  return typeof q === 'string' ? q : ''
}
</script>

<template>
  <ul v-if="tools.length" class="tools" data-testid="tool-status">
    <li v-for="t in tools" :key="t.callId" :class="'tool-' + t.status">
      <span class="tool-mark">{{ t.status === 'running' ? '⏳' : t.status === 'ok' ? '✓' : '✗' }}</span>
      <span class="tool-name">{{ toolLabel(t.name) }}</span>
      <span v-if="brief(t)" class="tool-arg" :title="brief(t)">{{ brief(t) }}</span>
      <span v-if="t.status === 'running'" class="muted small">进行中…</span>
      <span v-else-if="t.status === 'ok'" class="muted small">{{ t.summary }}{{ t.durationMs != null ? ` · ${(t.durationMs / 1000).toFixed(1)} s` : '' }}</span>
      <span v-else class="error small">失败：{{ t.error }}</span>
    </li>
  </ul>
</template>
