<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ApiError, http } from '../api/http'
import type { DocumentView } from '../api/types'
import { isPublic, kbStore, loadKbs } from '../stores/kbs'

const selectedId = ref<number | null>(null)
const docs = ref<DocumentView[]>([])
const newName = ref('')
const error = ref('')
const busy = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const selected = computed(() => kbStore.list.find((k) => k.id === selectedId.value) ?? null)
const inProgress = computed(() => docs.value.some((d) => d.status === 'PENDING' || d.status === 'PROCESSING'))

const STATUS_LABEL: Record<string, string> = {
  PENDING: '排队中',
  PROCESSING: '入库中',
  READY: '已就绪',
  FAILED: '失败',
}

function msg(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e)
}

async function refreshKbs() {
  try {
    await loadKbs()
    if (selectedId.value == null || !kbStore.list.some((k) => k.id === selectedId.value)) {
      selectedId.value = (kbStore.list.find((k) => !isPublic(k)) ?? kbStore.list[0])?.id ?? null
    }
  } catch (e) {
    error.value = msg(e)
  }
}

async function refreshDocs() {
  if (selectedId.value == null || !selected.value) return
  if (isPublic(selected.value)) {
    docs.value = []
    return
  }
  try {
    docs.value = await http.get<DocumentView[]>(`/api/kbs/${selectedId.value}/documents`)
  } catch (e) {
    error.value = msg(e)
  }
}

async function createKb() {
  const name = newName.value.trim()
  if (!name) return
  error.value = ''
  try {
    const kb = await http.post<{ id: number }>('/api/kbs', { name })
    newName.value = ''
    await refreshKbs()
    selectedId.value = kb.id
  } catch (e) {
    error.value = msg(e)
  }
}

async function deleteKb() {
  if (!selected.value || isPublic(selected.value)) return
  if (!window.confirm(`删除知识库「${selected.value.name}」及其全部文档和索引？此操作不可恢复。`)) return
  error.value = ''
  try {
    await http.del(`/api/kbs/${selected.value.id}`)
    selectedId.value = null
    await refreshKbs()
  } catch (e) {
    error.value = msg(e)
  }
}

async function upload(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !selected.value) return
  error.value = ''
  busy.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    await http.upload(`/api/kbs/${selected.value.id}/documents`, form)
    await refreshDocs()
  } catch (e) {
    error.value = msg(e)
  } finally {
    busy.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

async function deleteDoc(d: DocumentView) {
  if (!window.confirm(`删除文档「${d.filename}」？`)) return
  error.value = ''
  try {
    await http.del(`/api/documents/${d.id}`)
    await refreshDocs()
  } catch (e) {
    error.value = msg(e)
  }
}

function fmtSize(n: number): string {
  return n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`
}

let timer: number | undefined
onMounted(async () => {
  await refreshKbs()
  await refreshDocs()
  // 文档状态轮询：入库在后台进行（PENDING → PROCESSING → READY / FAILED）
  timer = window.setInterval(() => {
    if (inProgress.value) void refreshDocs()
  }, 2000)
})
onBeforeUnmount(() => window.clearInterval(timer))
watch(selectedId, () => {
  error.value = ''
  docs.value = []
  void refreshDocs()
})
</script>

<template>
  <div class="kb-page">
    <aside class="sidebar">
      <h2>知识库</h2>
      <ul class="list">
        <li v-for="kb in kbStore.list" :key="kb.id" :class="{ active: kb.id === selectedId }" @click="selectedId = kb.id">
          <span>{{ kb.name }}</span>
          <span v-if="isPublic(kb)" class="tag">公共 · 只读</span>
          <span v-else class="tag tag-private">私有</span>
        </li>
      </ul>
      <form class="row" @submit.prevent="createKb">
        <input v-model="newName" maxlength="100" placeholder="新建私有知识库" />
        <button type="submit" :disabled="!newName.trim()">新建</button>
      </form>
    </aside>

    <section class="content">
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <template v-if="selected">
        <div class="row between">
          <h2>{{ selected.name }}</h2>
          <button v-if="!isPublic(selected)" class="danger" type="button" @click="deleteKb">删除知识库</button>
        </div>

        <div v-if="isPublic(selected)" class="card">
          <p>
            公共库收录约 20 只医药医疗与科技主题基金的招募说明书、基金合同、年报与季报，由数据管线离线入库，这里不列出文件清单，也不能上传或删除。
          </p>
          <p class="muted small">在对话页可以选择是否检索公共库。</p>
        </div>

        <template v-else>
          <div class="row">
            <input ref="fileInput" type="file" accept=".pdf,.md,.markdown,.txt" :disabled="busy" @change="upload" />
            <span class="muted small">PDF / Markdown / TXT，单个文件不超过 20MB；同一库内相同内容不重复入库。</span>
          </div>
          <table v-if="docs.length" class="table">
            <thead>
              <tr><th>文件</th><th>大小</th><th>状态</th><th>页数 / 切块</th><th /></tr>
            </thead>
            <tbody>
              <tr v-for="d in docs" :key="d.id">
                <td>{{ d.filename }}</td>
                <td>{{ fmtSize(d.sizeBytes) }}</td>
                <td>
                  <span class="status" :class="'st-' + d.status.toLowerCase()">{{ STATUS_LABEL[d.status] ?? d.status }}</span>
                  <div v-if="d.status === 'FAILED' && d.error" class="error small">{{ d.error }}</div>
                </td>
                <td>{{ d.status === 'READY' ? `${d.pages ?? '-'} 页 / ${d.chunks ?? '-'} 块` : '—' }}</td>
                <td><button class="link" type="button" @click="deleteDoc(d)">删除</button></td>
              </tr>
            </tbody>
          </table>
          <p v-else class="muted">这个知识库还没有文档。上传后，对话页选中该库即可在其中检索。</p>
          <p class="muted small">私有库的检索质量没有做过评测，回答请以出处原文为准。</p>
        </template>
      </template>
    </section>
  </div>
</template>
