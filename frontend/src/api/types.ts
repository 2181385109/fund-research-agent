// 接口类型：以 docs/API.md 为准（backend 一节 + ai-service 的 SSE 事件协议）。

export interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
  requestId?: string
}

export interface AuthResult {
  token: string
  userId: number
  username: string
}

export interface Kb {
  id: number
  name: string
  /** PUBLIC | PRIVATE */
  type: string
  readOnly: boolean
  createdAt: string
}

export type DocStatus = 'PENDING' | 'PROCESSING' | 'READY' | 'FAILED'

export interface DocumentView {
  id: number
  kbId: number
  filename: string
  sizeBytes: number
  status: DocStatus
  error: string | null
  pages: number | null
  chunks: number | null
  createdAt: string
}

export interface Conversation {
  id: number
  title: string
  createdAt: string
  updatedAt: string
}

// ---- 出处：四类（docs/API.md「出处 citations.items[]」）----

export interface CitationBase {
  id: number
  kind: 'document' | 'database' | 'computation' | 'api'
}

export interface DocumentCitation extends CitationBase {
  kind: 'document'
  fund_code?: string | null
  fund_name?: string | null
  doc_id?: string
  doc_type?: string
  doc_title?: string
  report_period?: string | null
  page_start?: number | null
  page_end?: number | null
  section?: string | null
  snippet?: string
  /** 私有库片段才有 */
  kb_id?: string | number | null
}

export interface DatabaseCitation extends CitationBase {
  kind: 'database'
  tables?: string[]
  source?: string
  as_of?: string
  sql?: string
  row_count?: number
}

export interface ComputationCitation extends CitationBase {
  kind: 'computation'
  tool?: string
  args?: Record<string, unknown>
  share_code?: string
  start_used?: string
  end_used?: string
  source?: string
  as_of?: string
}

export interface ApiCitation extends CitationBase {
  kind: 'api'
  share_code?: string
  source?: string
  nav_date?: string
  fetched_at?: string
  stale?: boolean
}

export type Citation = DocumentCitation | DatabaseCitation | ComputationCitation | ApiCitation

export interface Message {
  id: number
  role: 'USER' | 'ASSISTANT' | string
  content: string
  citations: Citation[] | null
  disclaimer: string | null
  status: 'OK' | 'ERROR' | 'CANCELLED' | string | null
  requestId: string | null
  kbIds: number[] | null
  createdAt: string
}

// ---- SSE 事件 ----

export interface ToolCall {
  callId: string
  name: string
  args: Record<string, unknown>
  step: number
  status: 'running' | 'ok' | 'error'
  summary?: string
  error?: string
  durationMs?: number
}

export interface DoneInfo {
  status: 'ok' | 'error'
  timings_ms?: { total?: number; first_token?: number }
  usage?: { total_tokens?: number }
  request_model?: string
  response_models?: string[]
  tool_rounds?: number
  max_steps_reached?: boolean
  compliance_flags?: string[]
  /** 语义缓存开启时才有：true = 这次回答是从缓存回放的（没有调用 LLM） */
  cache_hit?: boolean
  cache_similarity?: number
}

export type ChatEvent =
  | { event: 'meta'; data: { request_id: string; model: string; max_steps: number } }
  | { event: 'tool_start'; data: { call_id: string; name: string; args: Record<string, unknown>; step: number } }
  | {
      event: 'tool_end'
      data: {
        call_id: string
        name: string
        status: 'ok' | 'error'
        duration_ms: number
        summary?: string
        error?: string
        citation_ids?: number[]
      }
    }
  | { event: 'token'; data: { text: string } }
  | { event: 'citations'; data: { items: Citation[] } }
  | { event: 'disclaimer'; data: { text: string } }
  | { event: 'done'; data: DoneInfo }
  | { event: 'error'; data: { code: string; message: string } }
