import { describe, expect, it } from 'vitest'
import { renderAnswer } from './render'

const kindOf = (id: number) => (id === 1 ? 'document' : id === 2 ? 'database' : undefined)

describe('renderAnswer', () => {
  it('[n] 变成按 kind 区分的角标，未知类型用中性角标', () => {
    const html = renderAnswer('托管人是工行 [1]，费率 0.6% [2][3]', kindOf)
    const div = document.createElement('div')
    div.innerHTML = html
    const cites = [...div.querySelectorAll('button.cite')] as HTMLElement[]
    expect(cites.map((c) => c.dataset.cite)).toEqual(['1', '2', '3'])
    expect(cites[0].className).toContain('cite-document')
    expect(cites[1].className).toContain('cite-database')
    expect(cites[2].className).toBe('cite')
  })

  it('流式过程中没收全的 "[" / "[1" 原样显示，不产生角标', () => {
    const div = document.createElement('div')
    div.innerHTML = renderAnswer('托管人是工行 [1', kindOf)
    expect(div.querySelector('.cite')).toBeNull()
    expect(div.textContent).toContain('[1')
  })

  it('代码块里的 [n] 不转换', () => {
    const div = document.createElement('div')
    div.innerHTML = renderAnswer('`a[1]`', kindOf)
    expect(div.querySelector('.cite')).toBeNull()
  })

  it('渲染 Markdown（加粗、列表）', () => {
    const div = document.createElement('div')
    div.innerHTML = renderAnswer('**工商银行**\n\n- 一\n- 二', kindOf)
    expect(div.querySelector('strong')?.textContent).toBe('工商银行')
    expect(div.querySelectorAll('li')).toHaveLength(2)
  })

  it('清洗 HTML：脚本、事件属性、图片都去掉；链接加 noopener', () => {
    const div = document.createElement('div')
    div.innerHTML = renderAnswer(
      '<script>alert(1)</script>\n\n<img src=x onerror=alert(1)><b onclick="x()">b</b>\n\n[详情](https://example.com)',
      kindOf,
    )
    expect(div.querySelector('script')).toBeNull()
    expect(div.querySelector('img')).toBeNull()
    expect(div.querySelector('[onclick]')).toBeNull()
    const a = div.querySelector('a')!
    expect(a.getAttribute('rel')).toContain('noopener')
    expect(a.getAttribute('target')).toBe('_blank')
  })
})
