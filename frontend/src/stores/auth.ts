import { reactive } from 'vue'
import { getToken, http, setToken, setUnauthorizedHandler } from '../api/http'
import type { AuthResult } from '../api/types'

const NAME_KEY = 'fra.username'

function readName(): string {
  try {
    return localStorage.getItem(NAME_KEY) ?? ''
  } catch {
    return ''
  }
}

export const auth = reactive({
  token: getToken(),
  username: readName(),
})

function apply(r: AuthResult): void {
  auth.token = r.token
  auth.username = r.username
  setToken(r.token)
  try {
    localStorage.setItem(NAME_KEY, r.username)
  } catch {
    /* ignore */
  }
}

export async function login(username: string, password: string): Promise<void> {
  apply(await http.post<AuthResult>('/api/auth/login', { username, password }))
}

export async function register(username: string, password: string): Promise<void> {
  apply(await http.post<AuthResult>('/api/auth/register', { username, password }))
}

export function logout(): void {
  auth.token = null
  auth.username = ''
  setToken(null)
}

/** 401（令牌过期 / 无效）：清登录态，路由守卫会把用户带回登录页 */
export function installUnauthorizedHandler(onLoggedOut: () => void): void {
  setUnauthorizedHandler(() => {
    logout()
    onLoggedOut()
  })
}
