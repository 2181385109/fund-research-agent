import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { Citation, Kb } from '../api/types'
import { FIXED_DISCLAIMER } from '../lib/constants'
import { newLiveAnswer } from '../lib/chatState'
import AssistantMessage from './AssistantMessage.vue'
import CitationList from './CitationList.vue'
import DisclaimerBar from './DisclaimerBar.vue'
import ScopePicker from './ScopePicker.vue'

const citations: Citation[] = [
  { id: 1, kind: 'document', fund_code: '003095', fund_name: '中欧医疗健康混合', doc_type: 'prospectus', doc_title: '招募说明书', page_start: 3, page_end: 3, snippet: '片段' },
  { id: 2, kind: 'database', tables: ['fund_fee'], source: 'akshare', as_of: '2026-09-28', sql: 'SELECT 1', row_count: 1 },
  { id: 3, kind: 'computation', share_code: '003095', start_used: '2025-12-31', end_used: '2026-06-30', source: 's', as_of: '2026-09-28' },
  { id: 4, kind: 'api', share_code: '003095', nav_date: '2026-09-28', fetched_at: 't', source: 'e', stale: false },
]

describe('DisclaimerBar', () => {
  it('固定文案，没有任何可点击的关闭 / 折叠控件', () => {
    const w = mount(DisclaimerBar)
    expect(w.text()).toContain(FIXED_DISCLAIMER)
    expect(w.findAll('button, summary, details, [role=button]')).toHaveLength(0)
  })
})

describe('AssistantMessage 风险提示', () => {
  it.each(['ok', 'error', 'cancelled', 'failed'] as const)('phase=%s 且服务端没有下发 disclaimer：显示固定文案', (phase) => {
    const w = mount(AssistantMessage, { props: { answer: { ...newLiveAnswer(), text: '答案', phase } } })
    expect(w.get('[data-testid=answer-disclaimer]').text()).toBe(FIXED_DISCLAIMER)
  })

  it('优先显示服务端下发的原文', () => {
    const w = mount(AssistantMessage, { props: { answer: { ...newLiveAnswer(), text: 'x', phase: 'ok', disclaimer: '服务端文案' } } })
    expect(w.get('[data-testid=answer-disclaimer]').text()).toBe('服务端文案')
  })

  it('流式进行中、尚未收到 disclaimer 时不重复显示（页面级常驻提示始终在）', () => {
    const w = mount(AssistantMessage, { props: { answer: { ...newLiveAnswer(), text: '半句' } } })
    expect(w.find('[data-testid=answer-disclaimer]').exists()).toBe(false)
  })

  it('点击正文角标展开并聚焦对应出处', async () => {
    const w = mount(AssistantMessage, {
      attachTo: document.body,
      props: { answer: { ...newLiveAnswer(), text: '费率 0.6% [2]', citations, phase: 'ok' } },
    })
    expect(w.find('.cite-detail').exists()).toBe(false)
    await w.get('button.cite').trigger('click')
    expect(w.get('.cite-card[data-cid="2"]').classes()).toContain('focus')
    expect(w.get('.cite-card[data-cid="2"] .cite-detail').text()).toContain('SELECT 1')
    w.unmount()
  })
})

describe('CitationList 四类出处', () => {
  it('四种 kind 各有图标和类型标签', () => {
    const w = mount(CitationList, { props: { citations } })
    const kinds = w.findAll('.cite-card').map((c) => c.attributes('data-kind'))
    expect(kinds).toEqual(['document', 'database', 'computation', 'api'])
    expect(w.findAll('.cite-icon').map((i) => i.text())).toEqual(['📄', '🗄️', '🧮', '🌐'])
    expect(w.findAll('.cite-kind').map((i) => i.text())).toEqual(['文档', '数据库', '计算', '接口'])
  })

  it('私有库片段标「私有库」，公共库片段标「公共库」，stale 的净值标「非最新」', () => {
    const w = mount(CitationList, {
      props: {
        citations: [
          citations[0],
          { id: 5, kind: 'document', kb_id: '11', doc_type: 'user_upload', doc_title: 'a.md', page_start: 1, page_end: 1 },
          { id: 6, kind: 'api', share_code: '1', stale: true },
        ],
      },
    })
    const tags = w.findAll('.cite-head .tag').map((t) => t.text())
    expect(tags).toEqual(['公共库', '私有库', '非最新'])
  })
})

describe('ScopePicker', () => {
  const kbs: Kb[] = [
    { id: 1, name: '基金披露文件', type: 'PUBLIC', readOnly: true, createdAt: '' },
    { id: 7, name: '我的笔记', type: 'PRIVATE', readOnly: false, createdAt: '' },
  ]
  it('区分公共库 / 私有库，勾选变化向上抛出选中的 id', async () => {
    const w = mount(ScopePicker, { props: { kbs, modelValue: [1, 7] } })
    const tags = w.findAll('.scope-item .tag').map((t) => t.text())
    expect(tags).toEqual(['公共库', '私有库'])
    await w.findAll('input')[1].setValue(false)
    expect(w.emitted('update:modelValue')![0]).toEqual([[1]])
  })

  it('一个都没选时提示', () => {
    const w = mount(ScopePicker, { props: { kbs, modelValue: [] } })
    expect(w.text()).toContain('至少选择一个检索范围')
  })
})
