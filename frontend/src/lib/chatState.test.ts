import { describe, expect, it } from 'vitest'
import type { ChatEvent } from '../api/types'
import { applyEvent, newLiveAnswer } from './chatState'

function run(events: ChatEvent[]) {
  return events.reduce(applyEvent, newLiveAnswer())
}

describe('applyEvent', () => {
  const events: ChatEvent[] = [
    { event: 'meta', data: { request_id: 'r1', model: 'deepseek-flash', max_steps: 6 } },
    { event: 'tool_start', data: { call_id: 'c1', name: 'search_fund_documents', args: { query: 'q' }, step: 1 } },
    { event: 'tool_start', data: { call_id: 'c2', name: 'run_fund_sql', args: { sql: 'select 1' }, step: 1 } },
    { event: 'tool_end', data: { call_id: 'c1', name: 'search_fund_documents', status: 'ok', duration_ms: 10, summary: '检索到 2 个片段', citation_ids: [1, 2] } },
    { event: 'tool_end', data: { call_id: 'c2', name: 'run_fund_sql', status: 'error', duration_ms: 5, error: 'boom' } },
    { event: 'token', data: { text: '答' } },
    { event: 'token', data: { text: '案 [1]' } },
    { event: 'citations', data: { items: [{ id: 1, kind: 'document' }] } },
    { event: 'disclaimer', data: { text: '风险提示' } },
    { event: 'done', data: { status: 'ok' } },
  ]

  it('累积文本、工具状态、出处、风险提示，done 后 phase=ok', () => {
    const s = run(events)
    expect(s.text).toBe('答案 [1]')
    expect(s.tools.map((t) => [t.callId, t.status])).toEqual([['c1', 'ok'], ['c2', 'error']])
    expect(s.tools[0].summary).toBe('检索到 2 个片段')
    expect(s.tools[1].error).toBe('boom')
    expect(s.citations).toHaveLength(1)
    expect(s.disclaimer).toBe('风险提示')
    expect(s.phase).toBe('ok')
    expect(s.requestId).toBe('r1')
  })

  it('tool_end 的 citation_ids 按工具名推断出处类型（citations 事件之前就能区分图标）', () => {
    const s = run(events.slice(0, 4))
    expect(s.kindHints).toEqual({ 1: 'document', 2: 'document' })
  })

  it('error 事件之后 done(status=error)：phase=error 且保留错误信息与风险提示', () => {
    const s = run([
      { event: 'error', data: { code: 'agent_error', message: '出错了' } },
      { event: 'disclaimer', data: { text: '风险提示' } },
      { event: 'done', data: { status: 'error' } },
    ])
    expect(s.phase).toBe('error')
    expect(s.error).toBe('出错了')
    expect(s.disclaimer).toBe('风险提示')
  })

  it('不修改入参', () => {
    const s0 = newLiveAnswer()
    applyEvent(s0, { event: 'token', data: { text: 'x' } })
    expect(s0.text).toBe('')
  })
})
