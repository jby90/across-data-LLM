import { ElMessage } from 'element-plus'
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
        {
          path: 'cockpit',
          name: 'cockpit',
          component: () => import('./views/Cockpit.vue'),
          meta: { roles: ['platform_admin'] },
        },
        { path: 'help', name: 'help', component: () => import('./views/Help.vue') },
        { path: 'updates', name: 'updates', component: () => import('./views/Updates.vue'), meta: { roles: ['platform_admin', 'ops_admin'] } },
        {
          path: 'sites',
          name: 'sites',
          component: () => import('./views/Sites.vue'),
          meta: { roles: ['platform_admin', 'ops_admin'] },
        },
        {
          path: 'documents',
          name: 'documents',
          component: () => import('./views/Documents.vue'),
          meta: { roles: ['platform_admin', 'ops_admin'] },
        },
        {
          path: 'standards',
          name: 'standards',
          component: () => import('./views/Standards.vue'),
          meta: { roles: ['platform_admin', 'ops_admin'] },
        },
        {
          path: 'opinion',
          name: 'opinion',
          component: () => import('./views/Opinion.vue'),
          meta: { roles: ['platform_admin', 'ops_admin'] },
        },
        {
          path: 'intake-quality',
          name: 'intake-quality',
          component: () => import('./views/IntakeQuality.vue'),
          meta: { roles: ['platform_admin', 'ops_admin'] },
        },
        {
          path: 'whitepapers',
          name: 'whitepapers',
          component: () => import('./views/Whitepapers.vue'),
          meta: { roles: ['platform_admin', 'ops_admin'] },
        },
        {
          path: 'admission',
          name: 'admission',
          component: () => import('./views/Admission.vue'),
          meta: { roles: ['platform_admin', 'ops_admin'] },
        },
        {
          path: 'audit-logs',
          name: 'audit-logs',
          component: () => import('./views/AuditLogs.vue'),
          meta: { roles: ['platform_admin'] },
        },
        {
          path: 'translations',
          name: 'translations',
          component: () => import('./views/Translations.vue'),
          meta: { roles: ['platform_admin', 'ops_admin'] },
        },
        { path: 'review-queue', redirect: '/translations' },
        {
          path: 'reviewer-changes',
          redirect: '/translations',
        },
        {
          path: 'knowledge-graph',
          name: 'knowledge-graph',
          component: () => import('./views/KnowledgeGraph.vue'),
          meta: { roles: ['platform_admin', 'ops_admin'] },
        },
        {
          path: 'document-templates',
          name: 'document-templates',
          component: () => import('./views/DocumentTemplates.vue'),
          meta: { roles: ['platform_admin', 'ops_admin'] },
        },
        {
          path: 'agent-studio',
          name: 'agent-studio',
          component: () => import('./views/AgentStudio.vue'),
          meta: { roles: ['platform_admin'] },
        },
        {
          path: 'orgs',
          name: 'orgs',
          component: () => import('./views/Orgs.vue'),
          meta: { roles: ['platform_admin'] },
        },
        // 企业与账号合并成一页：旧地址跳过去
        { path: 'users', name: 'users', redirect: '/orgs' },
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
  if (roles && !(auth.user && roles.includes(auth.user.role))) {
    ElMessage.warning('该页面仅限超级管理员访问')
    return { name: 'home' }
  }
  return true
})

export default router
