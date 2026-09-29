<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { Citation } from '../api/types'
import { citationDetails, citationMeta, citationTitle, isPrivateDoc } from '../lib/citations'
import { KIND_ICON, KIND_LABEL } from '../lib/render'

// 出处列表：每条一行摘要（图标按 kind 区分、角标编号、标题、来源与时点），点开看页码与片段 / 数据表与快照日期等。
// 编号与回答正文里的 [n] 角标一一对应；点击正文角标时通过 focusId 展开并滚动到对应条目。
const props = defineProps<{ citations: Citation[]; focusId?: number | null; focusTick?: number }>()

const open = ref<Set<number>>(new Set())

function toggle(id: number) {
  const s = new Set(open.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  open.value = s
}

const root = ref<HTMLElement | null>(null)
watch(
  () => [props.focusId, props.focusTick],
  async () => {
    const id = props.focusId
    if (id == null || !props.citations.some((c) => c.id === id)) return
    open.value = new Set(open.value).add(id)
    await nextTick()
    root.value?.querySelector(`[data-cid="${id}"]`)?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
  },
)
</script>

<template>
  <div v-if="citations.length" ref="root" class="citations" data-testid="citations">
    <div class="citations-title">出处（{{ citations.length }}）</div>
    <div
      v-for="c in citations"
      :key="c.id"
      class="cite-card"
      :class="[`cite-card-${c.kind}`, { focus: focusId === c.id }]"
      :data-cid="c.id"
      :data-kind="c.kind"
    >
      <button type="button" class="cite-head" :aria-expanded="open.has(c.id)" @click="toggle(c.id)">
        <span class="cite-icon" :title="KIND_LABEL[c.kind]">{{ KIND_ICON[c.kind] }}</span>
        <span class="cite-no">[{{ c.id }}]</span>
        <span class="cite-kind">{{ KIND_LABEL[c.kind] }}</span>
        <span v-if="c.kind === 'document'" class="tag" :class="{ 'tag-private': isPrivateDoc(c) }">
          {{ isPrivateDoc(c) ? '私有库' : '公共库' }}
        </span>
        <span v-if="c.kind === 'api' && c.stale" class="tag tag-warn">非最新</span>
        <span class="cite-title">{{ citationTitle(c) }}</span>
        <span class="cite-meta">{{ citationMeta(c) }}</span>
        <span class="cite-chevron">{{ open.has(c.id) ? '▾' : '▸' }}</span>
      </button>
      <dl v-if="open.has(c.id)" class="cite-detail">
        <template v-for="r in citationDetails(c)" :key="r.label">
          <dt>{{ r.label }}</dt>
          <dd :class="{ code: r.code }">{{ r.value }}</dd>
        </template>
      </dl>
    </div>
  </div>
</template>
