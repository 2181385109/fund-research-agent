import type { ApiEnvelope } from './types'

const TOKEN_KEY = 'fra.token'

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: number,
    message: string,
  ) {
    super(message)
  }
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* 隐私模式下 localStorage 不可用：本次会话内用不了，忽略 */
  }
}

/** 401 时的统一处理（由 auth store 注册：清登录态并跳转登录页） */
let onUnauthorized: () => void = () => {}
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn
}
export function notifyUnauthorized(): void {
  if (getToken()) onUnauthorized()
}

/** backend 的错误响应一律是 JSON {code,message}；不是 JSON（例如网关 502 的 HTML）时退回状态码文案。 */
export async function toApiError(res: Response): Promise<ApiError> {
  let code = res.status * 100
  let message = `请求失败（HTTP ${res.status}）`
  try {
    const body = await res.json()
    if (typeof body?.code === 'number') code = body.code
    if (typeof body?.message === 'string' && body.message) message = body.message
  } catch {
    /* 非 JSON */
  }
  return new ApiError(res.status, code, message)
}

export function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const t = getToken()
  return t ? { ...extra, Authorization: `Bearer ${t}` } : extra
}

export async function request<T>(method: string, path: string, body?: unknown | FormData): Promise<T> {
  const isForm = typeof FormData !== 'undefined' && body instanceof FormData
  const headers = authHeaders(body !== undefined && !isForm ? { 'Content-Type': 'application/json' } : {})
  let res: Response
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 0, '无法连接服务，请检查网络或稍后重试')
  }
  if (!res.ok) {
    const err = await toApiError(res)
    if (res.status === 401) notifyUnauthorized()
    throw err
  }
  if (res.status === 204) return undefined as T
  const env = (await res.json()) as ApiEnvelope<T>
  return env.data
}

export const http = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),
  upload: <T>(path: string, form: FormData) => request<T>('POST', path, form),
}
