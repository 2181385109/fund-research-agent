import type {
  ApiCitation,
  Citation,
  ComputationCitation,
  DatabaseCitation,
  DocumentCitation,
} from '../api/types'

const DOC_TYPE_LABEL: Record<string, string> = {
  prospectus: '招募说明书',
  contract: '基金合同',
  annual_report: '年度报告',
  quarterly_report: '季度报告',
  user_upload: '我的上传',
}

export function docTypeLabel(t?: string): string {
  return (t && DOC_TYPE_LABEL[t]) || t || '文档'
}

/** 私有库片段带 kb_id；公共库片段没有 */
export function isPrivateDoc(c: Citation): boolean {
  return c.kind === 'document' && (c as DocumentCitation).kb_id != null && (c as DocumentCitation).kb_id !== ''
}

export function pageRange(c: DocumentCitation): string {
  const a = c.page_start
  const b = c.page_end
  if (a == null) return ''
  return b != null && b !== a ? `第 ${a}–${b} 页` : `第 ${a} 页`
}

/** 一行摘要（折叠态显示） */
export function citationTitle(c: Citation): string {
  switch (c.kind) {
    case 'document': {
      const parts: string[] = []
      if (isPrivateDoc(c)) {
        parts.push(c.doc_title || '我的文件')
      } else {
        if (c.fund_name) parts.push(`${c.fund_name}${c.fund_code ? `（${c.fund_code}）` : ''}`)
        parts.push(docTypeLabel(c.doc_type) + (c.report_period ? ` ${c.report_period}` : ''))
      }
      const p = pageRange(c)
      if (p) parts.push(p)
      return parts.join(' · ')
    }
    case 'database':
      return `${(c.tables ?? []).join('、') || '基金数据库'}${c.row_count != null ? ` · ${c.row_count} 行` : ''}`
    case 'computation':
      return `${c.share_code ?? ''} 区间收益${c.start_used && c.end_used ? ` · ${c.start_used} → ${c.end_used}` : ''}`
    case 'api':
      return `${c.share_code ?? ''} 最新净值${c.nav_date ? ` · ${c.nav_date}` : ''}${c.stale ? ' · 非最新' : ''}`
  }
}

/** 数据来源与时点（折叠态的第二行）：文档的是文件名，数据类的是 source + 快照日期 / 抓取时间 */
export function citationMeta(c: Citation): string {
  switch (c.kind) {
    case 'document':
      return isPrivateDoc(c) ? '私有库文档' : (c.doc_title ?? '')
    case 'database':
    case 'computation':
      return [c.source, c.as_of ? `快照 ${c.as_of}` : ''].filter(Boolean).join(' · ')
    case 'api':
      return [c.source, c.fetched_at ? `抓取 ${c.fetched_at}` : ''].filter(Boolean).join(' · ')
  }
}

export type DetailRow = { label: string; value: string; code?: boolean }

/** 展开态的详情行（页码与片段 / 数据表与快照日期 / 计算参数 / 净值日期与抓取时间） */
export function citationDetails(c: Citation): DetailRow[] {
  const rows: DetailRow[] = []
  switch (c.kind) {
    case 'document': {
      const d = c as DocumentCitation
      rows.push({ label: '范围', value: isPrivateDoc(d) ? `私有库（知识库 ${d.kb_id}）` : '公共库（基金披露文件）' })
      if (d.doc_title) rows.push({ label: '文档', value: d.doc_title })
      const p = pageRange(d)
      if (p) rows.push({ label: '页码', value: p })
      if (d.section) rows.push({ label: '章节', value: d.section })
      if (d.snippet) rows.push({ label: '片段', value: d.snippet })
      break
    }
    case 'database': {
      const d = c as DatabaseCitation
      rows.push({ label: '数据表', value: (d.tables ?? []).join('、') })
      if (d.source) rows.push({ label: '数据源', value: d.source })
      if (d.as_of) rows.push({ label: '快照日期', value: d.as_of })
      if (d.row_count != null) rows.push({ label: '返回行数', value: String(d.row_count) })
      if (d.sql) rows.push({ label: 'SQL', value: d.sql, code: true })
      break
    }
    case 'computation': {
      const d = c as ComputationCitation
      rows.push({ label: '基金份额', value: d.share_code ?? '' })
      rows.push({ label: '实际起止净值日', value: `${d.start_used ?? '?'} → ${d.end_used ?? '?'}` })
      if (d.source) rows.push({ label: '数据源', value: d.source })
      if (d.as_of) rows.push({ label: '快照日期', value: d.as_of })
      if (d.args) rows.push({ label: '工具入参', value: JSON.stringify(d.args), code: true })
      break
    }
    case 'api': {
      const d = c as ApiCitation
      rows.push({ label: '基金份额', value: d.share_code ?? '' })
      if (d.nav_date) rows.push({ label: '净值日期', value: d.nav_date })
      if (d.fetched_at) rows.push({ label: '抓取时间', value: d.fetched_at })
      if (d.source) rows.push({ label: '数据源', value: d.source })
      if (d.stale) rows.push({ label: '注意', value: '接口不可用，已回退到快照，非最新净值' })
      break
    }
  }
  return rows.filter((r) => r.value !== '')
}
