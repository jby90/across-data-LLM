import { ApiError } from '@cyshuhe/shared'
import { createRouter, createWebHistory } from 'vue-router'
import { api } from './api'
import { useAuth } from './auth'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/Login.vue') },
    { path: '/licenses', name: 'licenses', component: () => import('./views/Licenses.vue') },
    {
      path: '/',
      component: () => import('./views/Layout.vue'),
      children: [
        { path: '', name: 'home', component: () => import('./views/Home.vue') },
        { path: 'notifications', name: 'notifications', component: () => import('./views/Notifications.vue') },
        { path: 'help', name: 'help', component: () => import('./views/Help.vue') },
        {
          path: 'knowledge',
          name: 'knowledge',
          component: () => import('./views/KnowledgeBase.vue'),
        },
        {
          path: 'analysis',
          name: 'analysis',
          component: () => import('./views/ChatAnalysis.vue'),
        },
        { path: 'documents', name: 'documents', component: () => import('./views/DocProjects.vue') },
        // 官方表格统一归入海外属地准入「官方申报原表」页签（支持按国浏览预览与在线填报）
        { path: 'forms', name: 'forms', redirect: { path: '/admission', query: { tab: 'forms' } } },
        { path: 'forms/:id', name: 'form', component: () => import('./views/Forms.vue') },
        {
          path: 'admission',
          name: 'admission',
          component: () => import('./views/Admission.vue'),
        },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  if (to.name === 'login' || to.name === 'licenses') return true
  if (!api.token()) return { name: 'login' }
  const auth = useAuth()
  // 只有登录失效（401/403）才退出；服务器一时忙或网络抖动时稍后再试一次，不把人踢回登录页
  for (let attempt = 0; ; attempt++) {
    try {
      await auth.load()
      break
    } catch (e) {
      if ((e instanceof ApiError && (e.status === 401 || e.status === 403)) || attempt >= 1) {
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) auth.logout()
        return { name: 'login' }
      }
      await new Promise((r) => setTimeout(r, 800))
    }
  }
  const roles = to.meta.roles as string[] | undefined
  if (roles && !(auth.user && roles.includes(auth.user.role))) return { name: 'home' }
  return true
})

export default router
