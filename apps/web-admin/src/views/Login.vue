<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from '../auth'
import AppIcon from '../components/AppIcon.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuth()
const form = reactive({ email: '', password: '' })
const loading = ref(false)

// 用户端地址：生产环境用 VITE_USER_URL 配置；开发环境默认同主机的 5174 端口
const userUrl: string =
  import.meta.env.VITE_USER_URL ??
  (import.meta.env.DEV ? `${location.protocol}//${location.hostname}:5174` : '')

onMounted(async () => {
  const ticket = typeof route.query.portal_ticket === 'string' ? route.query.portal_ticket.trim() : ''
  if (!ticket) return
  loading.value = true
  try {
    await auth.exchangePortalTicket(ticket)
    ElMessage.success('已通过单点凭证登录管理端')
    await router.replace({ name: 'home' })
  } catch (e) {
    ElMessage.error((e as Error).message || '单点登录凭证已失效，请使用账号密码登录')
  } finally {
    loading.value = false
  }
})

async function submit() {
  if (loading.value) return
  if (!form.email.trim() || !form.password) {
    ElMessage.warning('请输入邮箱和密码')
    return
  }
  loading.value = true
  try {
    await auth.login(form.email.trim(), form.password)
    await router.replace({ name: 'home' })
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}
</script>
<template>
  <div class="login-page">
    <section class="login-story">
      <div class="app-brand">
        <span class="brand-mark"
          ><svg viewBox="0 0 28 28" fill="none" aria-hidden="true">
            <path
              d="M5 9l9-5 9 5v10l-9 5-9-5V9z M5 9l9 5 9-5 M14 14v10"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linejoin="round"
            /></svg
        ></span>
        <div>
          <div class="brand-name">车研数合</div>
          <div class="brand-caption">汽车数据跨境合规平台</div>
        </div>
      </div>
      <div class="login-story-copy">
        <div class="eyebrow">汽车数据跨境法规与合规管理</div>
        <h1>让数据流动，<br />让决策有据。</h1>
        <p>连接全球法规、业务场景与合规行动，<br />让每一次跨境决策，都有清晰的依据。</p>
      </div>
      <svg class="login-art" viewBox="0 0 600 600" fill="none" aria-hidden="true">
        <circle cx="300" cy="300" r="270" stroke="currentColor" />
        <circle cx="300" cy="300" r="220" stroke="currentColor" />
        <ellipse cx="300" cy="300" rx="120" ry="270" stroke="currentColor" />
        <ellipse cx="300" cy="300" rx="270" ry="100" stroke="currentColor" />
        <path d="M30 300h540 M300 30v540 M105 105l390 390 M105 495l390-390" stroke="currentColor" />
        <circle cx="300" cy="30" r="8" fill="currentColor" />
        <circle cx="570" cy="300" r="8" fill="currentColor" />
      </svg>
      <div class="login-story-footer">法规知识库 · 合规评估 · 海外属地准入</div>
    </section>
    <main class="login-main">
      <div class="login-form">
        <div class="eyebrow">汽车数据跨境法规与合规管理</div>
        <h2>管理工作，从这里开始</h2>
        <p class="login-description">请使用超级管理员或运维管理员账号登录；企业用户请前往用户工作台。</p>
        <el-form label-position="top" @submit.prevent="submit">
          <el-form-item label="邮箱"
            ><el-input
              v-model="form.email"
              type="email"
              autocomplete="username"
              placeholder="请输入登录邮箱"
              aria-label="邮箱"
              ><template #prefix><AppIcon name="mail" /></template></el-input
          ></el-form-item>
          <el-form-item label="密码"
            ><el-input
              v-model="form.password"
              type="password"
              show-password
              autocomplete="current-password"
              placeholder="请输入密码"
              aria-label="密码"
              ><template #prefix><AppIcon name="lock" /></template></el-input
          ></el-form-item>
          <el-button
            class="login-submit"
            :class="{ 'is-ready': Boolean(form.email.trim() && form.password) }"
            type="primary"
            native-type="submit"
            :loading="loading"
            >登录工作台<AppIcon name="arrow" style="margin: 0 0 0 10px"
          /></el-button>
        </el-form>
        <div class="login-switch">
          企业用户或体验访客？<template v-if="userUrl"
            ><a :href="userUrl" data-testid="switch-to-user">前往用户工作台<AppIcon name="arrow" /></a
          ></template>
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.login-main {
  background: #ffffff;
}
.login-form {
  animation: none !important;
  opacity: 1 !important;
  transform: none !important;
}
.login-form .eyebrow {
  color: #1e40af;
  font-weight: 600;
  opacity: 1;
}
.login-form h2 {
  color: #0f172a;
  font-weight: 600;
  opacity: 1;
}
.login-description {
  color: #334155 !important;
  font-size: 14px;
  line-height: 1.65;
  opacity: 1;
}
.login-form :deep(.el-form-item__label) {
  color: #0f172a !important;
  font-weight: 600;
  font-size: 14px;
  opacity: 1;
}
.login-form :deep(.el-input__wrapper) {
  background-color: #ffffff;
  box-shadow: 0 0 0 1px #94a3b8 inset;
  transition: box-shadow 0.16s ease;
}
.login-form :deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px #475569 inset;
}
.login-form :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 2px #1d4ed8 inset !important;
}
.login-form :deep(.el-input__inner) {
  color: #0f172a;
  font-weight: 500;
}
.login-form :deep(.el-input__inner::placeholder) {
  color: #64748b;
  font-weight: 400;
}
.login-form :deep(.el-input .ui-icon) {
  color: #475569;
}
.login-submit {
  background-color: #1d4ed8 !important;
  border-color: #1d4ed8 !important;
  color: #ffffff !important;
  font-size: 15px;
  font-weight: 600 !important;
  letter-spacing: 0.02em;
  opacity: 1 !important;
  box-shadow: 0 4px 12px rgba(29, 78, 216, 0.24) !important;
}
.login-submit:hover,
.login-submit:focus-visible {
  background-color: #1e40af !important;
  border-color: #1e40af !important;
  color: #ffffff !important;
  box-shadow: 0 6px 16px rgba(30, 64, 175, 0.32) !important;
}
.login-submit.is-ready {
  background-color: #1e40af !important;
  border-color: #1e40af !important;
  box-shadow: 0 6px 18px rgba(29, 78, 216, 0.34) !important;
}
.login-submit.is-disabled,
.login-submit:disabled {
  background-color: #f1f5f9 !important;
  border-color: #cbd5e1 !important;
  color: #94a3b8 !important;
  box-shadow: none !important;
}
.login-switch {
  color: #334155;
  border-top-color: #cbd5e1;
}
.login-switch a {
  color: #1d4ed8;
  font-weight: 600;
}
.login-switch a:hover {
  color: #1e3a8a;
  text-decoration: underline;
}
</style>
