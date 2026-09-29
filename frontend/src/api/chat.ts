import { ApiError, authHeaders, notifyUnauthorized, toApiError } from './http'
import { readSse } from './sse'
import type { ChatEvent } from './types'

/**
 * POST /api/conversations/{id}/chat，返回 SSE。
 * 流开始之前的错误（401/403/404/503…）是普通 JSON：先看 HTTP 状态码，再解析流（docs/API.md）。
 * 流开始之后的错误是 SSE 的 error 事件，由调用方在 onEvent 里处理。
 */
export async function streamChat(
  conversationId: number,
  question: string,
  kbIds: number[] | undefined,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response
  try {
    res = await fetch(`/api/conversations/${conversationId}/chat`, {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json', Accept: 'text/event-stream' }),
      body: JSON.stringify(kbIds ? { question, kbIds } : { question }),
      signal,
    })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new ApiError(0, 0, '无法连接服务，请检查网络或稍后重试')
  }
  if (!res.ok) {
    const err = await toApiError(res)
    if (res.status === 401) notifyUnauthorized()
    throw err
  }
  if (!res.body) throw new ApiError(res.status, 0, '浏览器不支持流式读取响应')
  await readSse(res.body, (raw) => {
    let data: unknown
    try {
      data = JSON.parse(raw.data)
    } catch {
      return // 不是 JSON 的事件（协议里没有）：忽略
    }
    onEvent({ event: raw.event, data } as ChatEvent)
  })
}
