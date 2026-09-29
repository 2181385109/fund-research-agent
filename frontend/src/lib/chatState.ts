import type { ChatEvent, Citation, DoneInfo, ToolCall } from '../api/types'

export type CitationKind = Citation['kind']

/** 一次进行中（或刚结束）的助手回答。由 SSE 事件逐个 apply 得到。 */
export interface LiveAnswer {
  requestId: string | null
  model: string | null
  text: string
  tools: ToolCall[]
  citations: Citation[]
  /** 出处 id → 类型：citations 事件到达之前，先按工具名推断，让角标在流式过程中就能区分图标 */
  kindHints: Record<number, CitationKind>
  /** 服务端下发的风险提示原文（disclaimer 事件）；到达前为 null */
  disclaimer: string | null
  /** streaming：进行中；ok / error：done 事件；cancelled：用户点了停止；failed：连接中断（没有 done） */
  phase: 'streaming' | 'ok' | 'error' | 'cancelled' | 'failed'
  error: string | null
  done: DoneInfo | null
}

export function newLiveAnswer(): LiveAnswer {
  return {
    requestId: null,
    model: null,
    text: '',
    tools: [],
    citations: [],
    kindHints: {},
    disclaimer: null,
    phase: 'streaming',
    error: null,
    done: null,
  }
}

const TOOL_KIND: Record<string, CitationKind> = {
  search_fund_documents: 'document',
  run_fund_sql: 'database',
  calc_fund_return: 'computation',
  get_latest_nav: 'api',
}

export const TOOL_LABEL: Record<string, string> = {
  search_fund_documents: '检索披露文档',
  get_fund_db_schema: '查看数据表结构',
  run_fund_sql: '查询基金数据库',
  calc_fund_return: '计算区间收益',
  get_latest_nav: '查询最新净值',
}

export function toolLabel(name: string): string {
  return TOOL_LABEL[name] ?? name
}

/** 把一个 SSE 事件应用到状态上，返回新状态（不修改入参，便于测试和 Vue 响应式）。 */
export function applyEvent(s: LiveAnswer, e: ChatEvent): LiveAnswer {
  switch (e.event) {
    case 'meta':
      return { ...s, requestId: e.data.request_id, model: e.data.model }
    case 'tool_start':
      return {
        ...s,
        tools: [
          ...s.tools,
          { callId: e.data.call_id, name: e.data.name, args: e.data.args ?? {}, step: e.data.step, status: 'running' },
        ],
      }
    case 'tool_end': {
      const kind = TOOL_KIND[e.data.name]
      const hints = { ...s.kindHints }
      if (kind) for (const id of e.data.citation_ids ?? []) hints[id] = kind
      return {
        ...s,
        kindHints: hints,
        tools: s.tools.map((t) =>
          t.callId === e.data.call_id
            ? {
                ...t,
                status: e.data.status,
                summary: e.data.summary,
                error: e.data.error,
                durationMs: e.data.duration_ms,
              }
            : t,
        ),
      }
    }
    case 'token':
      return { ...s, text: s.text + e.data.text }
    case 'citations':
      return { ...s, citations: e.data.items }
    case 'disclaimer':
      return { ...s, disclaimer: e.data.text }
    case 'error':
      return { ...s, error: e.data.message }
    case 'done':
      return { ...s, done: e.data, phase: e.data.status === 'ok' ? 'ok' : 'error' }
    default:
      return s
  }
}
