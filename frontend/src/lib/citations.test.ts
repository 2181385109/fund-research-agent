import { describe, expect, it } from 'vitest'
import type { Citation } from '../api/types'
import { citationDetails, citationMeta, citationTitle, isPrivateDoc } from './citations'

const doc: Citation = {
  id: 1, kind: 'document', fund_code: '003095', fund_name: '中欧医疗健康混合', doc_id: 'x',
  doc_type: 'prospectus', doc_title: '招募说明书', report_period: '2026-09-21', page_start: 3, page_end: 4,
  section: '基金托管人', snippet: '托管人为中国工商银行股份有限公司',
}
const priv: Citation = {
  id: 2, kind: 'document', kb_id: '11', doc_type: 'user_upload', doc_title: '我的笔记.md', page_start: 1, page_end: 1, snippet: '内容',
}
const db: Citation = {
  id: 3, kind: 'database', tables: ['fund_fee'], source: 'akshare', as_of: '2026-09-28', sql: 'SELECT 1', row_count: 2,
}
const calc: Citation = {
  id: 4, kind: 'computation', tool: 'calc_fund_return', args: { share_code: '003095' }, share_code: '003095',
  start_used: '2025-12-31', end_used: '2026-06-30', source: 'eastmoney', as_of: '2026-09-28',
}
const api: Citation = {
  id: 5, kind: 'api', share_code: '003095', source: 'eastmoney', nav_date: '2026-09-28', fetched_at: '2026-09-29T10:00:00', stale: true,
}

describe('citations', () => {
  it('文档：页码、章节、片段；公共库与私有库分开标注', () => {
    expect(isPrivateDoc(doc)).toBe(false)
    expect(isPrivateDoc(priv)).toBe(true)
    expect(citationTitle(doc)).toBe('中欧医疗健康混合（003095） · 招募说明书 2026-09-21 · 第 3–4 页')
    expect(citationTitle(priv)).toBe('我的笔记.md · 第 1 页')
    const rows = Object.fromEntries(citationDetails(doc).map((r) => [r.label, r.value]))
    expect(rows['范围']).toBe('公共库（基金披露文件）')
    expect(rows['页码']).toBe('第 3–4 页')
    expect(rows['片段']).toContain('工商银行')
    expect(citationDetails(priv)[0].value).toBe('私有库（知识库 11）')
  })

  it('数据库：数据表、数据源、快照日期、SQL', () => {
    expect(citationTitle(db)).toBe('fund_fee · 2 行')
    expect(citationMeta(db)).toBe('akshare · 快照 2026-09-28')
    expect(citationDetails(db).find((r) => r.label === 'SQL')?.code).toBe(true)
  })

  it('计算：实际使用的起止净值日与入参', () => {
    expect(citationTitle(calc)).toBe('003095 区间收益 · 2025-12-31 → 2026-06-30')
    expect(citationDetails(calc).some((r) => r.label === '工具入参')).toBe(true)
  })

  it('接口：净值日期、抓取时间；stale 时明确标注非最新', () => {
    expect(citationTitle(api)).toContain('非最新')
    expect(citationMeta(api)).toContain('抓取 2026-09-29T10:00:00')
    expect(citationDetails(api).some((r) => r.label === '注意')).toBe(true)
  })
})
