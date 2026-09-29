// SSE 解析：fetch + ReadableStream（不能用 EventSource：要带 Authorization 头，且是 POST）。
// 规则见 WHATWG SSE：空行结束一个事件；以 ":" 开头的行是注释（backend 每 5 秒发 ": ping" 心跳），忽略；
// "data:" 可以多行；"event:" 后的空格可有可无；CRLF / LF / CR 都当作换行。

export interface RawSseEvent {
  event: string
  data: string
}

export class SseParser {
  private buffer = ''
  private event = ''
  private dataLines: string[] = []

  /** 喂入一段文本，返回其中已经完整的事件。块边界可以落在任意位置（包括行中间、CRLF 中间）。 */
  feed(chunk: string): RawSseEvent[] {
    this.buffer += chunk
    const out: RawSseEvent[] = []
    // 一个 "\r" 出现在缓冲末尾时可能是 "\r\n" 的前半，留到下次再处理
    const held = this.buffer.endsWith('\r') ? '\r' : ''
    const text = held ? this.buffer.slice(0, -1) : this.buffer
    const lines = text.split(/\r\n|\n|\r/)
    this.buffer = lines.pop()! + held
    for (const line of lines) {
      const ev = this.line(line)
      if (ev) out.push(ev)
    }
    return out
  }

  /** 流结束：缓冲里还有一个没有以空行收尾的事件时，按规范丢弃（不完整）。 */
  end(): void {
    this.buffer = ''
    this.event = ''
    this.dataLines = []
  }

  private line(line: string): RawSseEvent | null {
    if (line === '') {
      if (this.dataLines.length === 0 && !this.event) return null
      const ev = { event: this.event || 'message', data: this.dataLines.join('\n') }
      this.event = ''
      this.dataLines = []
      return ev
    }
    if (line.startsWith(':')) return null // 注释 / 心跳
    const i = line.indexOf(':')
    const field = i < 0 ? line : line.slice(0, i)
    let value = i < 0 ? '' : line.slice(i + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') this.event = value
    else if (field === 'data') this.dataLines.push(value)
    return null
  }
}

/** 读完整个响应体，逐个事件回调。signal 中止时抛出 AbortError（由调用方处理）。 */
export async function readSse(
  body: ReadableStream<Uint8Array>,
  onEvent: (e: RawSseEvent) => void,
): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder('utf-8')
  const parser = new SseParser()
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      for (const e of parser.feed(decoder.decode(value, { stream: true }))) onEvent(e)
    }
    const rest = decoder.decode()
    if (rest) for (const e of parser.feed(rest)) onEvent(e)
  } finally {
    parser.end()
    reader.releaseLock()
  }
}
