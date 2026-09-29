<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { streamChat } from '../api/chat'
import { ApiError, http } from '../api/http'
import type { Conversation, Message } from '../api/types'
import AssistantMessage from '../components/AssistantMessage.vue'
import DisclaimerBar from '../components/DisclaimerBar.vue'
import ScopePicker from '../components/ScopePicker.vue'
import { type LiveAnswer, applyEvent, newLiveAnswer } from '../lib/chatState'
import { describeScope, kbStore, loadKbs } from '../stores/kbs'

type Item = { kind: 'user'; text: string; kbIds: number[] | null } | { kind: 'assistant'; answer: LiveAnswer }

const route = useRoute()
const router = useRouter()

const conversations = ref<Conversation[]>([])
const items = ref<Item[]>([])
const input = ref('')
const sending = ref(false)
const pageError = ref('')
const selectedKbIds = ref<number[]>([])
const scroller = ref<HTMLElement | null>(null)
let abort: AbortController | null = null
let skipNextLoad = false

const SCOPE_KEY = 'fra.scope'
const currentId = computed(() => (route.params.id ? Number(route.params.id) : null))

const SUGGESTIONS = [
  '003095 从 2025-12-31 到 2026-06-30 的收益率是多少？',
  '中欧医疗健康混合的基金托管人是谁？',
  '医药医疗主题基金的招募说明书里，投资风险主要有哪些？',
]

function errMsg(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e)
}

function toItems(msgs: Message[]): Item[] {
  return msgs.map((m): Item => {
    if (m.role === 'USER') return { kind: 'user', text: m.content, kbIds: m.kbIds }
    const phase = m.status === 'ERROR' ? 'error' : m.status === 'CANCELLED' ? 'cancelled' : 'ok'
    return {
      kind: 'assistant',
      answer: {
        ...newLiveAnswer(),
        requestId: m.requestId,
        text: m.content,
        citations: m.citations ?? [],
        disclaimer: m.disclaimer,
        phase,
        error: phase === 'error' ? '这次回答失败了。' : null,
      },
    }
  })
}

async function loadConversations() {
  try {
    conversations.value = await http.get<Conversation[]>('/api/conversations')
  } catch (e) {
    pageError.value = errMsg(e)
  }
}

async function loadMessages(id: number | null) {
  items.value = []
  if (id == null) return
  try {
    items.value = toItems(await http.get<Message[]>(`/api/conversations/${id}/messages`))
    await scrollToBottom(true)
  } catch (e) {
    pageError.value = errMsg(e)
    if (e instanceof ApiError && e.status === 404) void router.replace('/chat')
  }
}

function loadScope() {
  const all = kbStore.list.map((k) => k.id)
  let selected: number[] = []
  let known: number[] = []
  try {
    const raw = JSON.parse(localStorage.getItem(SCOPE_KEY) ?? 'null') as { selected: number[]; known: number[] } | null
    if (raw) {
      selected = raw.selected.filter((id) => all.includes(id))
      known = raw.known
    }
  } catch {
    /* ignore */
  }
  // 默认 = 全部可见的库（公共库 + 我的私有库），与 backend 缺省语义一致；这里显式列出，让范围一目了然。
  // 之后用户的取消勾选会被记住；但上次访问之后新建的库默认勾选。
  const fresh = all.filter((id) => !known.includes(id))
  selectedKbIds.value = [...selected, ...fresh].filter((id, i, a) => a.indexOf(id) === i)
  if (selectedKbIds.value.length === 0) selectedKbIds.value = all
}

watch(selectedKbIds, (ids) => {
  try {
    localStorage.setItem(SCOPE_KEY, JSON.stringify({ selected: ids, known: kbStore.list.map((k) => k.id) }))
  } catch {
    /* ignore */
  }
})

async function scrollToBottom(force = false) {
  const el = scroller.value
  if (!el) return
  const near = el.scrollHeight - el.scrollTop - el.clientHeight < 120
  if (!force && !near) return
  await nextTick()
  el.scrollTop = el.scrollHeight
}

async function newChat() {
  if (sending.value) return
  await router.push('/chat')
}

async function removeConversation(c: Conversation) {
  if (!window.confirm(`删除对话「${c.title}」？`)) return
  try {
    await http.del(`/api/conversations/${c.id}`)
    if (currentId.value === c.id) await router.push('/chat')
    await loadConversations()
  } catch (e) {
    pageError.value = errMsg(e)
  }
}

function update(a: LiveAnswer) {
  const last = items.value[items.value.length - 1]
  if (last?.kind === 'assistant') last.answer = a
}

async function send(text?: string) {
  const question = (text ?? input.value).trim()
  if (!question || sending.value || selectedKbIds.value.length === 0) return
  pageError.value = ''
  sending.value = true
  const kbIds = [...selectedKbIds.value]
  try {
    let id = currentId.value
    if (id == null) {
      const c = await http.post<Conversation>('/api/conversations', {})
      id = c.id
      skipNextLoad = true // 路由变化只是换个地址，不要用空的历史覆盖正在显示的这轮对话
      await router.replace(`/chat/${id}`)
      void loadConversations() // 侧栏马上出现这个会话（标题在第一轮结束后更新）
    }
    input.value = ''
    items.value.push({ kind: 'user', text: question, kbIds })
    items.value.push({ kind: 'assistant', answer: newLiveAnswer() })
    await scrollToBottom(true)

    abort = new AbortController()
    let live = newLiveAnswer()
    try {
      await streamChat(
        id,
        question,
        kbIds,
        (ev) => {
          live = applyEvent(live, ev)
          update(live)
          void scrollToBottom()
        },
        abort.signal,
      )
      if (live.phase === 'streaming') update({ ...live, phase: 'failed', error: '连接中断，回答不完整。' })
    } catch (e) {
      if ((e as Error).name === 'AbortError') update({ ...live, phase: 'cancelled' })
      else update({ ...live, phase: 'failed', error: errMsg(e) })
    }
  } catch (e) {
    pageError.value = errMsg(e)
  } finally {
    sending.value = false
    abort = null
    void loadConversations() // 第一个问题会成为会话标题
  }
}

function stop() {
  abort?.abort()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    void send()
  }
}

watch(currentId, (id) => {
  if (skipNextLoad) {
    skipNextLoad = false
    return
  }
  pageError.value = ''
  void loadMessages(id)
})

onMounted(async () => {
  await Promise.all([loadConversations(), loadKbs().catch((e) => (pageError.value = errMsg(e)))])
  loadScope()
  await loadMessages(currentId.value)
})
</script>

<template>
  <div class="chat-page">
    <aside class="sidebar">
      <button class="primary" type="button" :disabled="sending" @click="newChat">＋ 新对话</button>
      <ul class="list">
        <li v-for="c in conversations" :key="c.id" :class="{ active: c.id === currentId }" @click="!sending && router.push(`/chat/${c.id}`)">
          <span class="ellipsis" :title="c.title">{{ c.title }}</span>
          <button class="link small" type="button" title="删除" @click.stop="removeConversation(c)">✕</button>
        </li>
      </ul>
    </aside>

    <section class="chat">
      <div ref="scroller" class="messages" data-testid="messages">
        <p v-if="pageError" class="error" role="alert">{{ pageError }}</p>
        <div v-if="!items.length" class="empty">
          <h2>问点什么？</h2>
          <p class="muted">可以问基金的费率、持仓、收益、披露文件里的条款，也可以在你上传的文件里检索。回答会标注每个数据的出处。</p>
          <div class="suggestions">
            <button v-for="s in SUGGESTIONS" :key="s" type="button" :disabled="sending" @click="send(s)">{{ s }}</button>
          </div>
        </div>
        <template v-for="(it, i) in items" :key="i">
          <div v-if="it.kind === 'user'" class="msg msg-user" data-testid="user-message">
            <div class="bubble">{{ it.text }}</div>
            <div class="scope-used muted small" data-testid="scope-used">检索范围：{{ describeScope(it.kbIds, kbStore.list) }}</div>
          </div>
          <AssistantMessage v-else :answer="it.answer" />
        </template>
      </div>

      <footer class="composer">
        <ScopePicker v-model="selectedKbIds" :kbs="kbStore.list" :disabled="sending" />
        <div class="row">
          <textarea
            v-model="input"
            rows="2"
            maxlength="2000"
            placeholder="输入问题，Enter 发送，Shift+Enter 换行"
            :disabled="sending"
            @keydown="onKeydown"
          />
          <button v-if="!sending" class="primary" type="button" :disabled="!input.trim() || selectedKbIds.length === 0" @click="send()">发送</button>
          <button v-else class="danger" type="button" @click="stop">停止</button>
        </div>
        <DisclaimerBar />
      </footer>
    </section>
  </div>
</template>
