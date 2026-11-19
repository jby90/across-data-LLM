import type { TokenResponse, UserOut } from '@cyshuhe/shared'
import { defineStore } from 'pinia'
import { api } from './api'

export const useAuth = defineStore('auth', {
  state: () => ({ user: null as UserOut | null }),
  actions: {
    async login(email: string, password: string) {
      const res = await api.login(email, password)
      api.setToken(res.access_token)
      this.user = await api.me()
    },
    async exchangePortalTicket(ticket: string) {
      const res = await api.post<TokenResponse>('/auth/admin/exchange', { ticket })
      api.setToken(res.access_token)
      this.user = await api.me()
    },
    async load() {
      if (!this.user && api.token()) this.user = await api.me()
    },
    async refreshMe() {
      if (api.token()) this.user = await api.me()
    },
    logout() {
      api.setToken(null)
      this.user = null
      try {
        sessionStorage.clear()
      } catch {
        /* ignore */
      }
      try {
        for (const k of Object.keys(localStorage)) {
          if (k.startsWith('cyshuhe.admin.') || k.startsWith('cysh:studio-layout:')) {
            localStorage.removeItem(k)
          }
        }
      } catch {
        /* ignore */
      }
    },
  },
})
