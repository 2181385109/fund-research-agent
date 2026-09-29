import DOMPurify from 'dompurify'
import { marked } from 'marked'
import type { CitationKind } from './chatState'

export const KIND_ICON: Record<CitationKind, string> = {
  document: '📄',
  database: '🗄️',
  computation: '🧮',
  api: '🌐',
}

export const KIND_LABEL: Record<CitationKind, string> = {
  document: '文档',
  database: '数据库',
  computation: '计算',
  api: '接口',
}

const SKIP_PARENTS = new Set(['CODE', 'PRE', 'A'])

/**
 * 回答（Markdown）→ 安全的 HTML；其中的 [n] 变成出处角标 <button class="cite cite-<kind>" data-cite="n">。
 * 只处理完整的 [数字]：流式过程中尚未收全的 "[" 或 "[1" 原样显示，下一次重渲染时才变成角标。
 * kindOf 未知（citations 事件之前也没有工具结果推断）时用中性角标。
 */
export function renderAnswer(text: string, kindOf: (id: number) => CitationKind | undefined): string {
  const html = marked.parse(text, { gfm: true, breaks: true, async: false }) as string
  const clean = DOMPurify.sanitize(html, { FORBID_TAGS: ['img', 'style', 'form', 'input', 'iframe'] })
  const tpl = document.createElement('template')
  tpl.innerHTML = clean

  const walker = document.createTreeWalker(tpl.content, NodeFilter.SHOW_TEXT)
  const targets: Text[] = []
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = n as Text
    if (/\[\d{1,3}\]/.test(t.data) && !SKIP_PARENTS.has(t.parentElement?.tagName ?? '')) targets.push(t)
  }
  for (const t of targets) {
    const frag = document.createDocumentFragment()
    let last = 0
    for (const m of t.data.matchAll(/\[(\d{1,3})\]/g)) {
      frag.append(t.data.slice(last, m.index))
      const id = Number(m[1])
      const kind = kindOf(id)
      const b = document.createElement('button')
      b.type = 'button'
      b.className = kind ? `cite cite-${kind}` : 'cite'
      b.dataset.cite = String(id)
      b.title = kind ? `出处 ${id}（${KIND_LABEL[kind]}）` : `出处 ${id}`
      b.textContent = `${kind ? KIND_ICON[kind] : ''}${id}`
      frag.append(b)
      last = (m.index ?? 0) + m[0].length
    }
    frag.append(t.data.slice(last))
    t.replaceWith(frag)
  }
  for (const a of tpl.content.querySelectorAll('a')) {
    a.setAttribute('target', '_blank')
    a.setAttribute('rel', 'noopener noreferrer')
  }
  return tpl.innerHTML
}
