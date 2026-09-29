import { describe, expect, it } from 'vitest'
import { SseParser, readSse } from './sse'

const SAMPLE =
  'event:meta\ndata:{"request_id": "r1"}\n\n' +
  ':ping\n\n' +
  'event:token\ndata:{"text": "中欧"}\n\n' +
  'event: done\ndata: {"status": "ok"}\n\n'

describe('SseParser', () => {
  it('解析事件，忽略注释心跳', () => {
    const evs = new SseParser().feed(SAMPLE)
    expect(evs.map((e) => e.event)).toEqual(['meta', 'token', 'done'])
    expect(JSON.parse(evs[1].data).text).toBe('中欧')
  })

  it('任意位置切块结果都相同（含 CRLF、行中间）', () => {
    const whole = new SseParser().feed(SAMPLE)
    for (const text of [SAMPLE, SAMPLE.replace(/\n/g, '\r\n')]) {
      for (let cut = 1; cut < text.length; cut++) {
        const p = new SseParser()
        const evs = [...p.feed(text.slice(0, cut)), ...p.feed(text.slice(cut))]
        expect(evs).toEqual(whole)
      }
    }
  })

  it('data 多行用换行连接；没有 event 字段时是 message', () => {
    const evs = new SseParser().feed('data: a\ndata: b\n\n')
    expect(evs).toEqual([{ event: 'message', data: 'a\nb' }])
  })

  it('末尾没有空行收尾的事件不算完整，不输出', () => {
    const p = new SseParser()
    expect(p.feed('event:token\ndata:{"text":"x"}\n')).toEqual([])
    p.end()
  })
})

describe('readSse', () => {
  it('按字节流读取，多字节字符被切在两个 chunk 之间也能还原', async () => {
    const bytes = new TextEncoder().encode('event:token\ndata:{"text":"基金"}\n\n')
    const cut = bytes.indexOf(0xe5) + 1 // 落在「基」的三字节中间
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(bytes.slice(0, cut))
        c.enqueue(bytes.slice(cut))
        c.close()
      },
    })
    const got: string[] = []
    await readSse(stream, (e) => got.push(JSON.parse(e.data).text))
    expect(got).toEqual(['基金'])
  })
})
