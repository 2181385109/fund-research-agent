<script setup lang="ts">
import { computed } from 'vue'
import type { Kb } from '../api/types'
import { isPublic } from '../stores/kbs'

// 检索范围选择：公共库（基金披露文件）与自己的私有库。选中的 id 作为 kbIds 发给 backend；
// 范围由服务端校验并注入到检索（ADR-043），前端只是「申请」，不是授权。
const props = defineProps<{ kbs: Kb[]; modelValue: number[]; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [ids: number[]] }>()

const publicKbs = computed(() => props.kbs.filter(isPublic))
const privateKbs = computed(() => props.kbs.filter((k) => !isPublic(k)))

function toggle(id: number, on: boolean) {
  const set = new Set(props.modelValue)
  if (on) set.add(id)
  else set.delete(id)
  emit('update:modelValue', props.kbs.filter((k) => set.has(k.id)).map((k) => k.id))
}
</script>

<template>
  <div class="scope" data-testid="scope-picker">
    <span class="scope-title">检索范围</span>
    <label v-for="kb in publicKbs" :key="kb.id" class="scope-item">
      <input type="checkbox" :checked="modelValue.includes(kb.id)" :disabled="disabled" @change="toggle(kb.id, ($event.target as HTMLInputElement).checked)" />
      <span class="tag">公共库</span> {{ kb.name }}
    </label>
    <label v-for="kb in privateKbs" :key="kb.id" class="scope-item">
      <input type="checkbox" :checked="modelValue.includes(kb.id)" :disabled="disabled" @change="toggle(kb.id, ($event.target as HTMLInputElement).checked)" />
      <span class="tag tag-private">私有库</span> {{ kb.name }}
    </label>
    <span v-if="!privateKbs.length" class="muted small">还没有私有库（在「知识库」页创建并上传文件）</span>
    <span v-if="modelValue.length === 0" class="error small">至少选择一个检索范围</span>
  </div>
</template>
