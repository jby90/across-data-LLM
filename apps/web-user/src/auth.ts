import type { TokenResponse, UserOut } from '@cyshuhe/shared'
import { defineStore } from 'pinia'
import { api } from './api'

const USER_STORAGE_PREFIXES = [
  'cyshuhe.copilot.',
  'cyshuhe.pchat.',
  'cyshuhe.papers-seen:',
  'papers-seen:',
  'chat-seen:',
]
const USER_STORAGE_KEYS = ['home-doc-progress-v2']

function clearBoundStorage(store: Storage) {
  try {
    const toRemove: string[] = []
    for (let i = 0; i < store.length; i++) {
      const k = store.key(i)
      if (!k) continue
      if (USER_STORAGE_KEYS.includes(k) || USER_STORAGE_PREFIXES.some((p) => k.startsWith(p))) {
        toRemove.push(k)
      }
    }
    for (const k of toRemove) store.removeItem(k)
  } catch {
    /* 浏览器禁用存储时静默跳过 */
  }
}

function clearUserSessionCaches() {
  clearBoundStorage(sessionStorage)
  clearBoundStorage(localStorage)
}

export const useAuth = defineStore('auth', {
  state: () => ({ user: null as UserOut | null }),
  actions: {
    async login(email: string, password: string) {
      clearUserSessionCaches()
      const res = await api.login(email, password)
      api.setToken(res.access_token)
      this.user = await api.me()
    },
    async register(payload: { email: string; display_name: string; org_name: string; password: string }) {
      clearUserSessionCaches()
      const res = await api.post<TokenResponse>('/auth/user/register', payload)
      api.setToken(res.access_token)
      this.user = await api.me()
    },
    async exchangePortalTicket(ticket: string) {
      clearUserSessionCaches()
      const res = await api.post<TokenResponse>('/auth/portal/exchange', { ticket })
      api.setToken(res.access_token)
      this.user = await api.me()
    },
    async refreshMe() {
      if (api.token()) this.user = await api.me()
    },
    async load() {
      if (!this.user && api.token()) this.user = await api.me()
    },
    logout() {
      api.setToken(null)
      this.user = null
      clearUserSessionCaches()
    },
  },
})
