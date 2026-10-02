<script setup lang="ts">
import { computed, ref } from 'vue'
import type { LiveAnswer } from '../lib/chatState'
import { FIXED_DISCLAIMER } from '../lib/constants'
import { renderAnswer } from '../lib/render'
import CitationList from './CitationList.vue'
import ToolStatus from './ToolStatus.vue'

const props = defineProps<{ answer: LiveAnswer }>()

const html = computed(() =>
  renderAnswer(props.answer.text, (id) => props.answer.citations.find((c) => c.id === id)?.kind ?? props.answer.kindHints[id]),
)

// 风险提示：优先显示服务端下发的原文；回答结束后仍没有（例如流开始前就失败）时显示固定文案，不留空。
const disclaimer = computed(() => props.answer.disclaimer || (props.answer.phase === 'streaming' ? '' : FIXED_DISCLAIMER))

const focusId = ref<number | null>(null)
const focusTick = ref(0)
function onAnswerClick(e: MouseEvent) {
  const el = (e.target as HTMLElement).closest('.cite') as HTMLElement | null
  if (!el?.dataset.cite) return
  focusId.value = Number(el.dataset.cite)
  focusTick.value++
}

const timing = computed(() => {
  const d = props.answer.done
  if (!d?.timings_ms?.total) return ''
  const parts = [`用时 ${(d.timings_ms.total / 1000).toFixed(1)} s`]
  if (d.timings_ms.first_token) parts.push(`首字 ${(d.timings_ms.first_token / 1000).toFixed(1)} s`)
  if (d.cache_hit) parts.push('命中缓存（未调用模型）')
  else if (d.usage?.total_tokens) parts.push(`${d.usage.total_tokens} tokens`)
  if (props.answer.model) parts.push(props.answer.model)
  return parts.join(' · ')
})
</script>

<template>
  <div class="msg msg-assistant" :class="'phase-' + answer.phase" data-testid="assistant-message">
    <ToolStatus :tools="answer.tools" />
    <div v-if="answer.text" class="answer" data-testid="answer" @click="onAnswerClick" v-html="html" />
    <p v-else-if="answer.phase === 'streaming'" class="muted">{{ answer.tools.length ? '正在整理答案…' : '思考中…' }}</p>
    <p v-if="answer.phase === 'cancelled'" class="muted small">已停止生成。</p>
    <p v-if="answer.phase === 'error' || answer.phase === 'failed'" class="error" role="alert">
      {{ answer.error || (answer.phase === 'failed' ? '连接中断，回答不完整。' : '回答失败。') }}
    </p>
    <p v-if="answer.done?.max_steps_reached" class="muted small">已达到工具调用轮数上限，答案可能不完整。</p>
    <CitationList :citations="answer.citations" :focus-id="focusId" :focus-tick="focusTick" />
    <div v-if="disclaimer" class="answer-disclaimer" data-testid="answer-disclaimer">{{ disclaimer }}</div>
    <div v-if="timing" class="muted small">{{ timing }}</div>
  </div>
</template>
